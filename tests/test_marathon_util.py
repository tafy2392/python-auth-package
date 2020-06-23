from typing import Any, Dict

import pytest  # type: ignore
from testtools import ExpectedException  # type: ignore
from testtools.assertions import assert_that  # type: ignore
from testtools.matchers import Equals  # type: ignore

from marathon_acme_trio.marathon_util import (  # type: ignore
    MLBDomainUtils,
    get_number_of_app_ports,
    parse_domain_label,
)

from .helpers import mkapp

TEST_APP: Dict[str, Any] = {
    "id": "/foovu1",
    "cmd": "python -m http.server 8080",
    "args": None,
    "user": None,
    "env": {},
    "instances": 1,
    "cpus": 0.1,
    "mem": 32,
    "disk": 0,
    "gpus": 0,
    "executor": "",
    "constraints": [],
    "uris": [],
    "fetch": [],
    "storeUrls": [],
    "backoffSeconds": 1,
    "backoffFactor": 1.15,
    "maxLaunchDelaySeconds": 3600,
    "container": None,
    "healthChecks": [],
    "readinessChecks": [],
    "dependencies": [],
    "upgradeStrategy": {"minimumHealthCapacity": 1, "maximumOverCapacity": 1},
    "labels": {},
    "ipAddress": None,
    "version": "2017-05-22T08:53:15.476Z",
    "residency": None,
    "secrets": {},
    "taskKillGracePeriodSeconds": None,
    "unreachableStrategy": {
        "inactiveAfterSeconds": 300,
        "expungeAfterSeconds": 600,
    },
    "killSelection": "YOUNGEST_FIRST",
    "versionInfo": {
        "lastScalingAt": "2017-05-22T08:53:15.476Z",
        "lastConfigChangeAt": "2017-05-22T08:53:15.476Z",
    },
    "tasksStaged": 0,
    "tasksRunning": 1,
    "tasksHealthy": 0,
    "tasksUnhealthy": 0,
    "deployments": [],
}

CONTAINER_HOST_NETWORKING = {
    "type": "DOCKER",
    "volumes": [],
    "docker": {
        "image": "praekeltfoundation/marathon-lb:1.6.0",
        "network": "HOST",
        "portMappings": [],
        "privileged": True,
        "parameters": [],
        "forcePullImage": False,
    },
}

CONTAINER_USER_NETWORKING = {
    "type": "DOCKER",
    "volumes": [],
    "docker": {
        "image": "python:3-alpine",
        "network": "USER",
        "portMappings": [
            {
                "containerPort": 8080,
                "servicePort": 10004,
                "protocol": "tcp",
                "name": "foovu1http",
                "labels": {"VIP_0": "/foovu1:8080"},
            },
        ],
        "privileged": False,
        "parameters": [],
        "forcePullImage": False,
    },
}

CONTAINER_BRIDGE_NETWORKING = {
    "type": "DOCKER",
    "volumes": [],
    "docker": {
        "image": "index.docker.io/jerithorg/testapp:0.0.12",
        "network": "BRIDGE",
        "portMappings": [
            {
                "containerPort": 5858,
                "hostPort": 0,
                "servicePort": 10008,
                "protocol": "tcp",
                "labels": {},
            },
        ],
        "privileged": False,
        "parameters": [],
        "forcePullImage": True,
    },
}

# We've never run a container with the Mesos containerizer before. This is from
# https://mesosphere.github.io/marathon/docs/external-volumes.html
CONTAINER_MESOS = {
    "type": "MESOS",
    "volumes": [
        {
            "containerPath": "test-rexray-volume",
            "external": {
                "size": 100,
                "name": "my-test-vol",
                "provider": "dvdi",
                "options": {"dvdi/driver": "rexray"},
            },
            "mode": "RW",
        },
    ],
}

# https://github.com/mesosphere/marathon/blob/v1.5.1/docs/docs/networking.md#host-mode
NETWORKS_CONTAINER_HOST_MARATHON15 = [{"mode": "host"}]
CONTAINER_MESOS_HOST_NETWORKING_MARATHON15 = {
    "type": "MESOS",
    "docker": {"image": "my-image:1.0"},
}

# https://github.com/mesosphere/marathon/blob/v1.5.1/docs/docs/networking.md#specifying-ports-1
NETWORKS_CONTAINER_BRIDGE_MARATHON15 = [{"mode": "container/bridge"}]
CONTAINER_BRIDGE_NETWORKING_MARATHON15 = {
    "type": "DOCKER",
    "docker": {
        "forcePullImage": True,
        "image": "praekeltfoundation/mc2:release-3.11.2",
        "parameters": [{"key": "add-host", "value": "servicehost:172.17.0.1"}],
        "privileged": False,
    },
    "volumes": [],
    "portMappings": [
        {
            "containerPort": 80,
            "hostPort": 0,
            "labels": {},
            "protocol": "tcp",
            "servicePort": 10005,
        }
    ],
}
CONTAINER_MESOS_BRIDGE_NETWORKING_MARATHON15 = {
    "type": "MESOS",
    "docker": {"image": "my-image:1.0"},
    "portMappings": [
        {"containerPort": 80, "hostPort": 0, "name": "http"},
        {"containerPort": 443, "hostPort": 0, "name": "https"},
        {"containerPort": 4000, "hostPort": 0, "name": "mon"},
    ],
}

# https://github.com/mesosphere/marathon/blob/v1.5.1/docs/docs/networking.md#enabling-container-mode
NETWORKS_CONTAINER_USER_MARATHON15 = [{"mode": "container", "name": "dcos"}]
CONTAINER_USER_NETWORKING_MARATHON15 = {
    "type": "DOCKER",
    "docker": {
        "forcePullImage": False,
        "image": "python:3-alpine",
        "parameters": [],
        "privileged": False,
    },
    "volumes": [],
    "portMappings": [
        {
            "containerPort": 8080,
            "labels": {"VIP_0": "/foovu1:8080"},
            "name": "foovu1http",
            "protocol": "tcp",
            "servicePort": 10004,
        }
    ],
}

IP_ADDRESS_NO_PORTS = {
    "groups": [],
    "labels": {},
    "discovery": {"ports": []},
    "networkName": "dcos",
}

IP_ADDRESS_TWO_PORTS = {
    "groups": [],
    "labels": {},
    "discovery": {
        "ports": [
            {"number": 80, "name": "http", "protocol": "tcp"},
            {"number": 443, "name": "http", "protocol": "tcp"},
        ],
    },
}

PORT_DEFINITIONS_ONE_PORT = [
    {"port": 10008, "protocol": "tcp", "name": "default", "labels": {}},
]


@pytest.fixture
def test_app():
    return TEST_APP.copy()


class TestGetNumberOfAppPortsFunc:
    def test_host_networking(self, test_app):
        """
        When the app uses Docker containers with HOST networking, the ports
        should be counted from the 'portDefinitions' field.
        """
        test_app["container"] = CONTAINER_HOST_NETWORKING
        test_app["portDefinitions"] = PORT_DEFINITIONS_ONE_PORT

        num_ports = get_number_of_app_ports(test_app)
        assert_that(num_ports, Equals(1))

    def test_user_networking(self, test_app):
        """
        When the app uses a Docker container with USER networking, it will have
        its 'ipAddress' field set. The ports should be counted using the
        'portMappings' field in the container definition.
        """
        test_app["container"] = CONTAINER_USER_NETWORKING
        test_app["ipAddress"] = IP_ADDRESS_NO_PORTS

        num_ports = get_number_of_app_ports(test_app)
        assert_that(num_ports, Equals(1))

    def test_ip_per_task_no_container(self, test_app):
        """
        When the app uses ip-per-task networking, but is not running in a
        container, then the ports should be counted from the 'ipAddress'
        field.
        """
        test_app["ipAddress"] = IP_ADDRESS_TWO_PORTS

        num_ports = get_number_of_app_ports(test_app)
        assert_that(num_ports, Equals(2))

    def test_ip_per_task_mesos_containerizer(self, test_app):
        """
        When the app uses ip-per-task networking, with the Mesos containerizer,
        the ports should be counted from the 'ipAddress' field.
        """
        test_app["container"] = CONTAINER_MESOS
        test_app["ipAddress"] = IP_ADDRESS_TWO_PORTS

        num_ports = get_number_of_app_ports(test_app)
        assert_that(num_ports, Equals(2))

    def test_bridge_networking(self, test_app):
        """
        When the app uses Docker containers with BRIDGE networking, the ports
        should be counted from the 'portDefinitions' field.
        """
        test_app["container"] = CONTAINER_BRIDGE_NETWORKING
        test_app["portDefinitions"] = PORT_DEFINITIONS_ONE_PORT

        num_ports = get_number_of_app_ports(test_app)
        assert_that(num_ports, Equals(1))

    def test_bridge_networking_no_port_definitions(self, test_app):
        """
        When the app uses Docker containers with BRIDGE networking, but the
        'portDefinitions' field is not defined, the ports should be counted
        from the 'ports' field.
        """
        test_app["container"] = CONTAINER_BRIDGE_NETWORKING
        test_app["ports"] = [10008, 10009]

        num_ports = get_number_of_app_ports(test_app)
        assert_that(num_ports, Equals(2))

    def test_host_networking_mesos_marathon15(self, test_app):
        """
        For Marathon 1.5+, when the app uses Mesos containers with host
        networking, the ports should be counted from the 'portDefinitions'
        field.
        """
        test_app["container"] = CONTAINER_MESOS_HOST_NETWORKING_MARATHON15
        test_app["networks"] = NETWORKS_CONTAINER_HOST_MARATHON15
        test_app["portDefinitions"] = PORT_DEFINITIONS_ONE_PORT

        num_ports = get_number_of_app_ports(test_app)
        assert_that(num_ports, Equals(1))

    def test_bridge_networking_marathon15(self, test_app):
        """
        For Marathon 1.5+, when the app uses Docker containers with
        'container/bridge' networking, the ports should be counted from the
        ``container.portMappings`` field.
        """
        test_app["container"] = CONTAINER_BRIDGE_NETWORKING_MARATHON15
        test_app["networks"] = NETWORKS_CONTAINER_BRIDGE_MARATHON15

        num_ports = get_number_of_app_ports(test_app)
        assert_that(num_ports, Equals(1))

    def test_bridge_networking_mesos_marathon15(self, test_app):
        """
        For Marathon 1.5+, when the app uses Mesos containers with
        'container/bridge' networking, the ports should be counted from the
        ``container.portMappings`` field.
        """
        test_app["container"] = CONTAINER_MESOS_BRIDGE_NETWORKING_MARATHON15
        test_app["networks"] = NETWORKS_CONTAINER_BRIDGE_MARATHON15

        num_ports = get_number_of_app_ports(test_app)
        assert_that(num_ports, Equals(3))

    def test_user_networking_marathon15(self, test_app):
        """
        For Marathon 1.5+, when the app uses Docker containers with 'container'
        networking, the ports should be counted from the
        ``container.portMappings`` field.
        """
        test_app["container"] = CONTAINER_USER_NETWORKING_MARATHON15
        test_app["networks"] = NETWORKS_CONTAINER_USER_MARATHON15

        num_ports = get_number_of_app_ports(test_app)
        assert_that(num_ports, Equals(1))

    def test_unknown_networking_mode(self, test_app):
        """
        When an app is defined with an unknown networking mode, an error is
        raised.
        """
        test_app["networks"] = [{"mode": "container/iptables"}]

        with ExpectedException(
            RuntimeError,
            r"Unknown Marathon networking mode 'container/iptables'",
        ):
            get_number_of_app_ports(test_app)


class TestParseDomainLabel:
    def test_single_domain(self):
        """
        When the domain label contains just a single domain, that domain should
        be parsed into a list containing just the one domain.
        """
        domains = parse_domain_label("example.com")
        assert_that(domains, Equals(["example.com"]))

    def test_separators(self):
        """
        When the domain label contains only the separators (commas or
        whitespace), the separators should be ignored.
        """
        domains = parse_domain_label(" , ,   ")
        assert_that(domains, Equals([]))

    def test_multiple_domains_comma(self):
        """
        When the domain label contains multiple comma-separated domains, the
        domains should be parsed into a list of domains.
        """
        domains = parse_domain_label("example.com,example2.com")
        assert_that(domains, Equals(["example.com", "example2.com"]))

    def test_multiple_domains_whitespace(self):
        """
        When the domain label contains multiple whitespace-separated domains,
        the domains should be parsed into a list of domains.
        """
        domains = parse_domain_label("example.com example2.com")
        assert_that(domains, Equals(["example.com", "example2.com"]))

    def test_multiple_domains_comma_whitespace(self):
        """
        When the domain label contains multiple comma-separated domains with
        whitespace inbetween, the domains should be parsed into a list of
        domains without the whitespace.
        """
        domains = parse_domain_label(" example.com, example2.com ")
        assert_that(domains, Equals(["example.com", "example2.com"]))


class TestMLBDomainUtils:
    def test_one_app_one_domain(self):
        """
        When we have one app with one domain, just that domain is returned.
        """
        mlb_utils = MLBDomainUtils()
        apps = [mkapp("/my-app_1", MARATHON_ACME_0_DOMAIN="example.com")]
        assert sorted(mlb_utils._apps_acme_domains(apps)) == ["example.com"]

    def test_one_app_two_domains(self):
        """
        When we have one app with two domains, both domains are returned.
        """
        mlb_utils = MLBDomainUtils()
        apps = [
            mkapp(
                "/my-app_1",
                ports=[9000, 9001],
                MARATHON_ACME_0_DOMAIN="example.com",
                MARATHON_ACME_1_DOMAIN="example2.com",
            )
        ]
        assert sorted(mlb_utils._apps_acme_domains(apps)) == [
            "example.com",
            "example2.com",
        ]

    def test_two_apps(self):
        """
        When we have two apps, domains for both are returned.
        """
        mlb_utils = MLBDomainUtils()
        apps = [
            mkapp("/my-app_1", MARATHON_ACME_0_DOMAIN="example.com"),
            mkapp("/my-app_2", MARATHON_ACME_0_DOMAIN="example2.com"),
        ]
        assert sorted(mlb_utils._apps_acme_domains(apps)) == [
            "example.com",
            "example2.com",
        ]

    def test_common_domains(self):
        """
        When we have multiple common domains, all copies are returned.
        """
        mlb_utils = MLBDomainUtils()
        apps = [
            mkapp(
                "/my-app_1",
                ports=[9000, 9001],
                MARATHON_ACME_0_DOMAIN="example.com",
                MARATHON_ACME_1_DOMAIN="example.com",
            ),
            mkapp(
                "/my-app_2",
                ports=[8000],
                MARATHON_ACME_0_DOMAIN="example.com",
            ),
        ]
        assert sorted(mlb_utils._apps_acme_domains(apps)) == [
            "example.com",
            "example.com",
            "example.com",
        ]

    def test_multiple_domains_in_one_label(self):
        """
        When we have a label containing multiple domains, all are returned.
        """
        mlb_utils = MLBDomainUtils()
        apps = [mkapp("/my-app_1", MARATHON_ACME_0_DOMAIN="ex.com,ex2.com")]
        assert sorted(mlb_utils._apps_acme_domains(apps)) == [
            "ex.com",
            "ex2.com",
        ]

    def test_multiple_domains_in_one_label_when_disallowed(self):
        """
        When we have a label containing multiple domains and we disallow
        multiple certs, only the first is returned.
        """
        mlb_utils = MLBDomainUtils(allow_multiple_certs=False)
        apps = [mkapp("/my-app_1", MARATHON_ACME_0_DOMAIN="ex.com,ex2.com")]
        assert sorted(mlb_utils._apps_acme_domains(apps)) == ["ex.com"]

    def test_one_domain_when_multiple_disallowed(self):
        """
        When we have a label containing one domain and we disallow multiple
        certs, the one domain is returned.
        """
        mlb_utils = MLBDomainUtils(allow_multiple_certs=False)
        apps = [mkapp("/my-app_1", MARATHON_ACME_0_DOMAIN="example.com")]
        assert sorted(mlb_utils._apps_acme_domains(apps)) == ["example.com"]

    def test_no_domains(self):
        """
        When we have an app containing no domains, an empty list is returned.
        """
        mlb_utils = MLBDomainUtils()
        apps = [mkapp("/my-app_1", group=None, HAPROXY_0_VHOST="example.com")]
        assert sorted(mlb_utils._apps_acme_domains(apps)) == []

    def test_empty_domain(self):
        """
        When we have an app containing an empty domain label, an empty list is
        returned.
        """
        mlb_utils = MLBDomainUtils()
        apps = [mkapp("/my-app_1", MARATHON_ACME_0_DOMAIN="")]
        assert sorted(mlb_utils._apps_acme_domains(apps)) == []

    def test_group_mismatch(self):
        """
        When we have an app containing a different group name, an empty list is
        returned.
        """
        mlb_utils = MLBDomainUtils()
        apps = [
            mkapp(
                "/my-app_1",
                group="internal",
                HAPROXY_0_VHOST="example.com",
                MARATHON_ACME_0_DOMAIN="example.com",
            )
        ]
        assert sorted(mlb_utils._apps_acme_domains(apps)) == []

    def test_port_group_mismatch(self):
        """
        When we have an app containing a different group name for the only port
        with a domain, an empty list is returned.
        """
        mlb_utils = MLBDomainUtils()
        apps = [
            mkapp(
                "/my-app_1",
                HAPROXY_0_GROUP="internal",
                HAPROXY_0_VHOST="example.com",
                MARATHON_ACME_0_DOMAIN="example.com",
            )
        ]
        assert sorted(mlb_utils._apps_acme_domains(apps)) == []
