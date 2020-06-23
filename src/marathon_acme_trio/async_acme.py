from functools import partial, wraps

import trio
from acme.challenges import HTTP01  # type: ignore
from acme.client import ClientNetwork, ClientV2  # type: ignore
from acme.messages import Directory, NewRegistration  # type: ignore

USER_AGENT = "marathon-acme-trio"


async def _thread(f, *args, **kw):
    return await trio.to_thread.run_sync(partial(f, *args, **kw))


def _thread_wrap(f):
    @wraps(f)
    async def wrapper(*args, **kw):
        return await trio.to_thread.run_sync(partial(f, *args, **kw))

    return wrapper


class _ClientV2PlusPlus(ClientV2):
    def get_or_create_account(self, new_account):
        # ClientV2 doesn't actually support this, so we have to reimplement it
        # by copying all the bits of new_account that don't raise exceptions.
        response = self._post(self.directory["newAccount"], new_account)
        # if account already exists, don't raise ConflictError.
        # "Instance of 'Field' has no key/contact member" bug:
        regr = self._regr_from_response(response)
        self.net.account = regr
        return regr


class _ClientV2Wrapper:
    def __init__(self, client):
        self._client = client
        self.get_or_create_account = _thread_wrap(client.get_or_create_account)
        self.new_order = _thread_wrap(client.new_order)
        self.answer_challenge = _thread_wrap(client.answer_challenge)
        self.poll_and_finalize = _thread_wrap(client.poll_and_finalize)

    @classmethod
    def build_sync(cls, directory_url, account_key, **kw):
        net = ClientNetwork(account_key, user_agent=USER_AGENT, **kw)
        directory = Directory.from_json(net.get(directory_url).json())
        return cls(_ClientV2PlusPlus(directory, net=net))

    @classmethod
    async def build(cls, directory_url, account_key, **kw):
        return await _thread(cls.build_sync, directory_url, account_key, **kw)


class AsyncAcme:
    def __init__(self, client, account_key):
        self.client = client
        self._client = client._client
        self.account_key = account_key

    @classmethod
    async def _build(cls, directory_url, account_key, **kw):
        client = await _ClientV2Wrapper.build(directory_url, account_key, **kw)
        return cls(client, account_key)

    @classmethod
    async def build(cls, directory_url, account_key, email, **kw):
        self = await cls._build(directory_url, account_key, **kw)
        # We don't need the account object, but we do need to make the call to
        # set internal client state.
        await self.get_or_create_account(email)
        return self

    async def get_or_create_account(self, email):
        msg = NewRegistration.from_data(
            email=email, terms_of_service_agreed=True
        )
        return await self.client.get_or_create_account(msg)

    async def new_order(self, csr_pem):
        return await self.client.new_order(csr_pem)

    async def answer_challenge(self, challenge):
        response = challenge.response(self.account_key)
        return await self.client.answer_challenge(challenge, response)

    async def poll_and_finalize(self, order):
        return await self.client.poll_and_finalize(order)


def get_http01(order):
    for auth in order.authorizations:
        for challenge in auth.body.challenges:
            if isinstance(challenge.chall, HTTP01):
                return challenge
