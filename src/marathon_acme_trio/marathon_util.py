import logging


def get_number_of_app_ports(app):
    """
    Get the number of ports for the given app JSON. This roughly follows the
    logic in marathon-lb for finding app IPs/ports, although we are only
    interested in the quantity of ports an app should have and don't consider
    the specific IPs/ports of individual tasks:
    https://github.com/mesosphere/marathon-lb/blob/v1.10.3/utils.py#L393-L415

    :param app: The app JSON from the Marathon API.
    :return: The number of ports for the app.
    """
    mode = _get_networking_mode(app)
    ports_list = None
    if mode == "host":
        ports_list = _get_port_definitions(app)
    elif mode == "container/bridge":
        ports_list = _get_port_definitions(app)
        if ports_list is None:
            ports_list = _get_container_port_mappings(app)
    elif mode == "container":
        ports_list = _get_ip_address_discovery_ports(app)
        # Marathon 1.5+: the ipAddress field is missing -> ports_list is None
        # Marathon <1.5: the ipAddress field can be present, but ports_list can
        # still be empty while the container port mapping is not :-/
        if not ports_list:
            ports_list = _get_container_port_mappings(app)
    else:
        raise RuntimeError(
            "Unknown Marathon networking mode '{}'".format(mode)
        )

    return len(ports_list)


def _get_networking_mode(app):
    """
    Get the Marathon networking mode for the app.
    """
    # Marathon 1.5+: there is a `networks` field
    networks = app.get("networks")
    if networks:
        # Modes cannot be mixed, so assigning the last mode is fine
        return networks[-1].get("mode", "container")

    # Older Marathon: determine equivalent network mode
    container = app.get("container")
    if container is not None and "docker" in container:
        docker_network = container["docker"].get("network")
        if docker_network == "USER":
            return "container"
        elif docker_network == "BRIDGE":
            return "container/bridge"

    return "container" if _is_legacy_ip_per_task(app) else "host"


def _get_container_port_mappings(app):
    """
    Get the ``portMappings`` field for the app container.
    """
    container = app["container"]

    # Marathon 1.5+: container.portMappings field
    port_mappings = container.get("portMappings")

    # Older Marathon: container.docker.portMappings field
    if port_mappings is None and "docker" in container:
        port_mappings = container["docker"].get("portMappings")

    return port_mappings


def _get_port_definitions(app):
    """
    Get the ``portDefinitions`` field for the app if present.
    """
    if "portDefinitions" in app:
        return app["portDefinitions"]

    # In the worst case try use the old `ports` array
    # Only useful on very old Marathons
    if "ports" in app:
        return app["ports"]

    return None


def _get_ip_address_discovery_ports(app):
    """
    Get the ports from the ``ipAddress`` field for the app if present.
    """
    if not _is_legacy_ip_per_task(app):
        return None
    return app["ipAddress"]["discovery"]["ports"]


def _is_legacy_ip_per_task(app):
    """
    Return whether the application is using IP-per-task on Marathon < 1.5.
    :param app: The application to check.
    :return: True if using IP per task, False otherwise.
    """
    return app.get("ipAddress") is not None


# Extra methods from marathon-acme
def parse_domain_label(domain_label):
    """ Parse the list of comma-separated domains from the app label. """
    return domain_label.replace(",", " ").split()


class MLBDomainUtils(object):
    def __init__(self, group="external", allow_multiple_certs=True):
        self.group = group
        self.log = logging.getLogger(__name__.split(".")[0])
        self._allow_multiple_certs = allow_multiple_certs

    def _log_debug(self, msg, **kw):
        # Easier to add this wrapper than rewrite all the log calls.
        self.log.debug(msg.format(**kw))

    def _log_warning(self, msg, **kw):
        # Easier to add this wrapper than rewrite all the log calls.
        self.log.warning(msg.format(**kw))

    def _apps_acme_domains(self, apps):
        domains = []
        for app in apps:
            domains.extend(self._app_acme_domains(app))

        self._log_debug(
            "Found {len_domains} domains for apps: {domains}",
            len_domains=len(domains),
            domains=domains,
        )

        return domains

    def _app_acme_domains(self, app):
        app_domains = []
        labels = app["labels"]
        app_group = labels.get("HAPROXY_GROUP")

        # Iterate through the ports, checking for corresponding labels
        for port_index in range(get_number_of_app_ports(app)):
            # Get the port group label, defaulting to the app group label
            port_group = labels.get(
                "HAPROXY_%d_GROUP" % (port_index,), app_group
            )

            if port_group == self.group:
                domain_label = labels.get(
                    "MARATHON_ACME_%d_DOMAIN" % (port_index,), ""
                )
                port_domains = parse_domain_label(domain_label)

                # TODO: Support multiple domains per certificate (SAN).
                if self._allow_multiple_certs:
                    app_domains.extend(port_domains)
                elif port_domains:
                    if len(port_domains) > 1:
                        self._log_warning(
                            "Multiple domains found for port {port} of app "
                            "{app}, only the first will be used",
                            port=port_index,
                            app=app["id"],
                        )

                    app_domains.append(port_domains[0])

        self._log_debug(
            "Found {len_domains} domains for app {app}: {domains}",
            len_domains=len(app_domains),
            app=app["id"],
            domains=app_domains,
        )

        return app_domains
