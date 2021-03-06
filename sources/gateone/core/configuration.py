# -*- coding: utf-8 -*-
#
#       Copyright 2013 Liftoff Software Corporation

# Meta
__license__ = "AGPLv3 or Proprietary (see LICENSE.txt)"
__doc__ = """
.. _settings.py:

Settings Module for Gate One
============================

This module contains functions that deal with Gate One's options/settings
"""

import os, sys, io, re, socket, tempfile, logging
from gateone import GATEONE_DIR
from .log import FACILITIES
from gateone.core.log import go_logger
from tornado import locale
from tornado.escape import json_decode
from tornado.options import define, options, Error

# Locale stuff (can't use .locale since .locale uses this module)
# Default to using the environment's locale with en_US fallback
temp_locale = locale.get(os.environ.get('LANG', 'en_US').split('.')[0])
_ = temp_locale.translate
del temp_locale

logger = go_logger(None)
comments_re = re.compile(
    r'//.*?$|/\*.*?\*/|\'(?:\\.|[^\\\'])*\'|"(?:\\.|[^\\"])*"',
    re.DOTALL | re.MULTILINE
)
trailing_commas_re = re.compile(
    r'(,)\s*}(?=([^"\\]*(\\.|"([^"\\]*\\.)*[^"\\]*"))*[^"]*$)')

class SettingsError(Exception):
    """
    Raised when we encounter an error parsing .conf files in the settings dir.
    """
    pass

class RUDict(dict):
    """
    A dict that will recursively update keys and values in a safe manner so that
    sub-dicts will be merged without one clobbering the other.

    .. note::

        This class (mostly) taken from `here
        <http://stackoverflow.com/questions/6256183/combine-two-dictionaries-of-dictionaries-python>`_
    """
    def __init__(self, *args, **kw):
        super(RUDict,self).__init__(*args, **kw)

    def update(self, E=None, **F):
        if E is not None:
            if 'keys' in dir(E) and callable(getattr(E, 'keys')):
                for k in E:
                    if k in self:  # Existing ...must recurse into both sides
                        self.r_update(k, E)
                    else: # Doesn't currently exist, just update
                        self[k] = E[k]
            else:
                for (k, v) in E:
                    self.r_update(k, {k:v})

        for k in F:
            self.r_update(k, {k:F[k]})

    def r_update(self, key, other_dict):
        if isinstance(self[key], dict) and isinstance(other_dict[key], dict):
            od = RUDict(self[key])
            nd = other_dict[key]
            od.update(nd)
            self[key] = od
        else:
            self[key] = other_dict[key]

    def __repr__(self):
        """
        Returns the `RUDict` as indented json to better resemble how it looks in
        a .conf file.
        """
        import json # Tornado's json_encode doesn't do indentation
        return json.dumps(self, indent=4)

    def __str__(self):
        """
        Just returns `self.__repr__()` with an extra newline at the end.
        """
        return self.__repr__() + "\n"

# Utility functions (copied from utils.py so we don't have an import paradox)
def generate_session_id():
    """
    Returns a random, 45-character session ID.  Example:

    .. code-block:: python

        >>> generate_session_id()
        "NzY4YzFmNDdhMTM1NDg3Y2FkZmZkMWJmYjYzNjBjM2Y5O"
        >>>
    """
    import base64, uuid
    from tornado.escape import utf8
    session_id = base64.b64encode(
        utf8(uuid.uuid4().hex + uuid.uuid4().hex))[:45]
    if bytes != str: # Python 3
        return str(session_id, 'UTF-8')
    return session_id

def mkdir_p(path):
    """
    Pythonic version of "mkdir -p".  Example equivalents::

        >>> mkdir_p('/tmp/test/testing') # Does the same thing as...
        >>> from subprocess import call
        >>> call('mkdir -p /tmp/test/testing')

    .. note:: This doesn't actually call any external commands.
    """
    import errno
    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            pass
        else: raise

# Settings and options-related functions
# NOTE:  "options" refer to command line arguments (for the most part) while
# "settings" refers to the .conf files.
def define_options(installed=True):
    """
    Calls `tornado.options.define` for all of Gate One's command-line options.

    If *installed* is ``False`` the defaults will be set under the assumption
    that the user is non-root and running Gate One out of a download/cloned
    directory.
    """
    # NOTE: To test this function interactively you must import tornado.options
    # and call tornado.options.parse_config_file(*some_config_path*).  After you
    # do that the options will wind up in tornado.options.options
    global user_locale
    # Default to using the shell's LANG variable as the locale
    try:
        default_locale = os.environ['LANG'].split('.')[0]
    except KeyError: # $LANG isn't set
        default_locale = "en_US"
    user_locale = locale.get(default_locale)
    # NOTE: The locale setting above is only for the --help messages.
    # Simplify the auth option help message
    auths = "none, api, google, ssl"
    from gateone.auth.authentication import PAMAuthHandler, KerberosAuthHandler
    if KerberosAuthHandler:
        auths += ", kerberos"
    if PAMAuthHandler:
        auths += ", pam"
    # Simplify the syslog_facility option help message
    facilities = list(FACILITIES.keys())
    facilities.sort()
    # Figure out the default origins
    default_origins = [
        'localhost',
        '127.0.0.1',
    ]
    # Used both http and https above to demonstrate that both are acceptable
    try:
        additional_origins = socket.gethostbyname_ex(socket.gethostname())
    except socket.gaierror:
        # Couldn't get any IPs from the hostname
        additional_origins = []
    for host in additional_origins:
        if isinstance(host, str):
            default_origins.append('%s' % host)
        else: # It's a list
            for _host in host:
                default_origins.append('%s' % _host)
    default_origins = ";".join(default_origins)
    config_default = os.path.join(os.path.sep, "opt", "gateone", "server.conf")
    # NOTE: --settings_dir deprecates --config
    settings_base = os.path.join(os.path.sep, 'etc', 'gateone')
    settings_default = os.path.join(settings_base, 'conf.d')
    port_default = 443
    log_default = os.path.join(
        os.path.sep, "var", "log", 'gateone', 'gateone.log')
    user_dir_default = os.path.join(
        os.path.sep, "var", "lib", "gateone", "users")
    pid_default = os.path.join(os.path.sep, "var", "run", 'gateone.pid')
    session_dir_default = os.path.join(tempfile.gettempdir(), 'gateone')
    cache_dir_default = os.path.join(tempfile.gettempdir(), 'gateone_cache')
    if os.getuid() != 0: # Not root?  Use $HOME/.gateone/ for everything
        home = os.path.expanduser('~')
        user_dir_default = os.path.join(home, '.gateone')
        settings_default = os.path.join(user_dir_default, 'conf.d')
        port_default = 10443
        log_default = os.path.join(user_dir_default, 'logs', 'gateone.log')
        pid_default = os.path.join(user_dir_default, 'gateone.pid')
        session_dir_default = os.path.join(user_dir_default, 'sessions')
        cache_dir_default = os.path.join(user_dir_default, 'cache')
    if not installed:
        # Running inside the download directory?  Change various defaults to
        # work inside of this directory
        here = os.path.dirname(os.path.abspath(__file__))
        settings_base = os.path.normpath(os.path.join(here, '..', '..'))
        settings_default = os.path.join(settings_base, 'conf.d')
        port_default = 10443
        log_default = os.path.join(settings_base, 'logs', 'gateone.log')
        user_dir_default = os.path.join(settings_base, 'users')
        pid_default = os.path.join(settings_base, 'gateone.pid')
        session_dir_default = os.path.join(settings_base, 'sessions')
        cache_dir_default = os.path.join(settings_base, 'cache')
    options.log_file_prefix = log_default
    ssl_dir = os.path.join(settings_base, 'ssl')
    define("version",
        type=bool,
        group='gateone',
        help=_("Display version information."),
    )
    define("config",
        default=config_default,
        group='gateone',
        help=_("DEPRECATED.  Use --settings_dir."),
        type=basestring,
    )
    define("settings_dir",
        default=settings_default,
        group='gateone',
        help=_("Path to the settings directory."),
        type=basestring
    )
    define(
        "cache_dir",
        default=cache_dir_default,
        group='gateone',
        help=_(
            "Path where Gate One should store temporary global files (e.g. "
            "rendered templates, CSS, JS, etc)."),
        type=basestring
    )
    define(
        "debug",
        default=False,
        group='gateone',
        help=_("Enable debugging features such as auto-restarting when files "
               "are modified.")
    )
    define("cookie_secret", # 45 chars is, "Good enough for me" (cookie joke =)
        default=None,
        group='gateone',
        help=_("Use the given 45-character string for cookie encryption."),
        type=basestring
    )
    define("command",
        default=None,
        group='gateone',
        help=_(
            "DEPRECATED: Use the 'commands' option in the terminal settings."),
        type=basestring
    )
    define("address",
        default="",
        group='gateone',
        help=_("Run on the given address.  Default is all addresses (IPv6 "
               "included).  Multiple address can be specified using a semicolon"
               " as a separator (e.g. '127.0.0.1;::1;10.1.1.100')."),
        type=basestring)
    define("port",
           default=port_default,
           group='gateone',
           help=_("Run on the given port."),
           type=int)
    define(
        "enable_unix_socket",
        default=False,
        group='gateone',
        help=_("Enable Unix socket support."),
        type=bool)
    define(
        "unix_socket_path",
        default="/tmp/gateone.sock",
        group='gateone',
        help=_("Path to the Unix socket (if --enable_unix_socket=True)."),
        type=basestring)
    # Please only use this if Gate One is running behind something with SSL:
    define(
        "disable_ssl",
        default=False,
        group='gateone',
        help=_("If enabled, Gate One will run without SSL (generally not a "
               "good idea).")
    )
    define(
        "certificate",
        default=os.path.join(ssl_dir, "certificate.pem"),
        group='gateone',
        help=_("Path to the SSL certificate.  Will be auto-generated if none is"
               " provided."),
        type=basestring
    )
    define(
        "keyfile",
        default=os.path.join(ssl_dir, "keyfile.pem"),
        group='gateone',
        help=_("Path to the SSL keyfile.  Will be auto-generated if none is"
               " provided."),
        type=basestring
    )
    define(
        "ca_certs",
        default=None,
        group='gateone',
        help=_("Path to a file containing any number of concatenated CA "
               "certificates in PEM format.  They will be used to authenticate "
               "clients if the 'ssl_auth' option is set to 'optional' or "
               "'required'."),
        type=basestring
    )
    define(
        "ssl_auth",
        default='none',
        group='gateone',
        help=_("Enable the use of client SSL (X.509) certificates as a "
               "secondary authentication factor (the configured 'auth' type "
               "will come after SSL auth).  May be one of 'none', 'optional', "
               "or 'required'.  NOTE: Only works if the 'ca_certs' option is "
               "configured."),
        type=basestring
    )
    define(
        "user_dir",
        default=user_dir_default,
        group='gateone',
        help=_("Path to the location where user files will be stored."),
        type=basestring
    )
    define(
        "user_logs_max_age",
        default="30d",
        group='gateone',
        help=_("Maximum amount of length of time to keep any given user log "
                "before it is removed."),
        type=basestring
    )
    define(
        "session_dir",
        default=session_dir_default,
        group='gateone',
        help=_(
            "Path to the location where session information will be stored."),
        type=basestring
    )
    define(
        "syslog_facility",
        default="daemon",
        group='gateone',
        help=_("Syslog facility to use when logging to syslog (if "
               "syslog_session_logging is enabled).  Must be one of: %s."
               % ", ".join(facilities)),
        type=basestring
    )
    define(
        "session_timeout",
        default="5d",
        group='gateone',
        help=_("Amount of time that a session is allowed to idle before it is "
        "killed.  Accepts <num>X where X could be one of s, m, h, or d for "
        "seconds, minutes, hours, and days.  Set to '0' to disable the ability "
        "to resume sessions."),
        type=basestring
    )
    define(
        "new_api_key",
        default=False,
        group='gateone',
        help=_("Generate a new API key that an external application can use to "
               "embed Gate One."),
    )
    define(
        "auth",
        default="none",
        group='gateone',
        help=_("Authentication method to use.  Valid options are: %s" % auths),
        type=basestring
    )
    # This is to prevent replay attacks.  Gate One only keeps a "working memory"
    # of API auth objects for this amount of time.  So if the Gate One server is
    # restarted we don't have to write them to disk as anything older than this
    # setting will be invalid (no need to check if it has already been used).
    define(
        "api_timestamp_window",
        default="30s", # 30 seconds
        group='gateone',
        help=_(
            "How long before an API authentication object becomes invalid.  "),
        type=basestring
    )
    define(
        "sso_realm",
        default=None,
        group='gateone',
        help=_("Kerberos REALM (aka DOMAIN) to use when authenticating clients."
               " Only relevant if Kerberos authentication is enabled."),
        type=basestring
    )
    define(
        "sso_service",
        default='HTTP',
        group='gateone',
        help=_("Kerberos service (aka application) to use. Defaults to HTTP. "
               "Only relevant if Kerberos authentication is enabled."),
        type=basestring
    )
    define(
        "pam_realm",
        default=os.uname()[1],
        group='gateone',
        help=_("Basic auth REALM to display when authenticating clients.  "
        "Default: hostname.  "
        "Only relevant if PAM authentication is enabled."),
        # NOTE: This is only used to show the user a REALM at the basic auth
        #       prompt and as the name in the GATEONE_DIR+'/users' directory
        type=basestring
    )
    define(
        "pam_service",
        default='login',
        group='gateone',
        help=_("PAM service to use.  Defaults to 'login'. "
               "Only relevant if PAM authentication is enabled."),
        type=basestring
    )
    define(
        "embedded",
        default=False,
        group='gateone',
        help=_(
            "When embedding Gate One, this option is available to templates.")
    )
    define(
        "locale",
        default=default_locale,
        group='gateone',
        help=_("The locale (e.g. pt_PT) Gate One should use for translations."
             "  If not provided, will default to $LANG (which is '%s' in your "
             "current shell), or en_US if not set."
             % os.environ.get('LANG', 'not set').split('.')[0]),
        type=basestring
    )
    define("js_init",
        default="",
        group='gateone',
        help=_("A JavaScript object (string) that will be used when running "
               "GateOne.init() inside index.html.  "
               "Example: --js_init=\"{scheme: 'white'}\" would result in "
               "GateOne.init({scheme: 'white'})"),
        type=basestring
    )
    define(
        "https_redirect",
        default=False,
        group='gateone',
        help=_("If enabled, a separate listener will be started on port 80 that"
               " redirects users to the configured port using HTTPS.")
    )
    define(
        "url_prefix",
        default="/",
        group='gateone',
        help=_("An optional prefix to place before all Gate One URLs. e.g. "
               "'/gateone/'.  Use this if Gate One will be running behind a "
               "reverse proxy where you want it to be located at some sub-"
               "URL path."),
        type=basestring
    )
    define(
        "origins",
        default=default_origins,
        group='gateone',
        help=_("A semicolon-separated list of origins you wish to allow access "
               "to your Gate One server over the WebSocket.  This value must "
               "contain the hostnames and FQDNs (e.g. foo;foo.bar;) users will"
               " use to connect to your Gate One server as well as the "
               "hostnames/FQDNs of any sites that will be embedding Gate One. "
               "Alternatively, '*' may be  specified to allow access from "
               "anywhere."),
        type=basestring
    )
    define(
        "pid_file",
        default=pid_default,
        group='gateone',
        help=_(
            "Define the path to the pid file.  Default: /var/run/gateone.pid"),
        type=basestring
    )
    define(
        "uid",
        default=str(os.getuid()),
        group='gateone',
        help=_(
            "Drop privileges and run Gate One as this user/uid."),
        type=basestring
    )
    define(
        "gid",
        default=str(os.getgid()),
        group='gateone',
        help=_(
            "Drop privileges and run Gate One as this group/gid."),
        type=basestring
    )
    define(
        "api_keys",
        default="",
        group='gateone',
        help=_("The 'key:secret,...' API key pairs you wish to use (only "
               "applies if using API authentication)"),
        type=basestring
    )
    define(
        "combine_js",
        default="",
        group='gateone',
        help=_(
            "Combines all of Gate One's JavaScript files into one big file and "
            "saves it at the given path (e.g. ./gateone.py "
            "--combine_js=/tmp/gateone.js)"),
        type=basestring
    )
    define(
        "combine_css",
        default="",
        group='gateone',
        help=_(
            "Combines all of Gate One's CSS Template files into one big file "
            "and saves it at the given path (e.g. ./gateone.py "
            "--combine_css=/tmp/gateone.css)."),
        type=basestring
    )
    define(
        "combine_css_container",
        default="gateone",
        group='gateone',
        help=_(
            "Use this setting in conjunction with --combine_css if the <div> "
            "where Gate One lives is named something other than #gateone"),
        type=basestring
    )

def settings_template(path, **kwargs):
    """
    Renders and returns the Tornado template at *path* using the given *kwargs*.

    .. note:: Any blank lines in the rendered template will be removed.
    """
    from tornado.template import Template
    with io.open(path, mode='r', encoding='utf-8') as f:
        template_data = f.read()
    t = Template(template_data)
    # NOTE: Tornado returns templates as bytes, not unicode.  That's why we need
    # the decode() below...
    rendered = t.generate(**kwargs).decode('utf-8')
    out = ""
    for line in rendered.splitlines():
        if line.strip():
            out += line + "\n"
    return out

def parse_commands(commands):
    """
    Given a list of *commands* (which can include arguments) such as::

        ['ls', '--color="always"', '-lh', 'ps', '--context', '-ef']

    Returns an `OrderedDict` like so::

        OrderedDict([
            ('ls', ['--color="always"', '-ltrh']),
            ('ps', ['--context', '-ef'])
        ])
    """
    try:
        from collections import OrderedDict
    except ImportError: # Python <2.7 didn't have OrderedDict in collections
        from ordereddict import OrderedDict
    out = OrderedDict()
    command = OrderedDict()
    for item in commands:
        if item.startswith('-'):
            out[command].append(item)
        else:
            command = item
            out[command] = []
    return out

def generate_server_conf(installed=True):
    """
    Generates a fresh settings/10server.conf file using the arguments provided
    on the command line to override defaults.

    If *installed* is ``False`` the defaults will be set under the assumption
    that the user is non-root and running Gate One out of a download/cloned
    directory.
    """
    logger.info(_(
        u"Gate One settings are incomplete.  A new settings/10server.conf"
        u" will be generated."))
    auth_settings = {} # Auth stuff goes in 20authentication.conf
    all_setttings = options_to_settings(options) # NOTE: options is global
    settings_path = options.settings_dir
    server_conf_path = os.path.join(settings_path, '10server.conf')
    if os.path.exists(server_conf_path):
        logger.error(_(
            "You have a 10server.conf but it is either invalid (syntax "
            "error) or missing essential settings."))
        sys.exit(1)
    config_defaults = all_setttings['*']['gateone']
    # Don't need this in the actual settings file:
    del config_defaults['settings_dir']
    non_options = [
        # These are things that don't really belong in settings
        'new_api_key', 'help', 'kill', 'config'
    ]
    # Don't need non-options in there either:
    for non_option in non_options:
        if non_option in config_defaults:
            del config_defaults[non_option]
    # Generate a new cookie_secret
    config_defaults['cookie_secret'] = generate_session_id()
    # Separate out the authentication settings
    authentication_options = [
        # These are here only for logical separation in the .conf files
        'api_timestamp_window', 'auth', 'pam_realm', 'pam_service',
        'sso_keytab', 'sso_realm', 'sso_service', 'ssl_auth'
    ]
    # Provide some kerberos (sso) defaults
    auth_settings['sso_realm'] = "EXAMPLE.COM"
    auth_settings['sso_keytab'] = None # Allow /etc/krb5.conf to control it
    for key, value in list(config_defaults.items()):
        if key in authentication_options:
            auth_settings.update({key: value})
            del config_defaults[key]
        if key == 'origins':
            # As a convenience to the user, add any --port to the origins
            if config_defaults['port'] not in [80, 443]:
                for i, origin in enumerate(list(value)):
                    value[i] = "{origin}:{port}".format(
                        origin=origin, port=config_defaults['port'])
    # Make sure we have a valid log_file_prefix
    if config_defaults['log_file_prefix'] == None:
        web_log_dir = os.path.join(os.path.sep, "var", "log", "gateone")
        if installed:
            here = os.path.dirname(os.path.abspath(__file__))
            web_log_dir = os.path.normpath(
                os.path.join(here, '..', '..', 'logs'))
        web_log_path = os.path.join(web_log_dir, 'gateone.log')
        config_defaults['log_file_prefix'] = web_log_path
    else:
        web_log_dir = os.path.split(config_defaults['log_file_prefix'])[0]
    if not os.path.exists(web_log_dir):
        # Make sure the directory exists
        mkdir_p(web_log_dir)
    if not os.path.exists(config_defaults['log_file_prefix']):
        # Make sure the file is present
        io.open(
            config_defaults['log_file_prefix'],
            mode='w', encoding='utf-8').write(u'')
    auth_conf_path = os.path.join(settings_path, '20authentication.conf')
    template_path = os.path.join(
        GATEONE_DIR, 'templates', 'settings', '10server.conf')
    new_settings = settings_template(
        template_path, settings=config_defaults)
    with io.open(server_conf_path, mode='w') as s:
        s.write(u"// This is Gate One's main settings file.\n")
        s.write(new_settings)
    new_auth_settings = settings_template(
        template_path, settings=auth_settings)
    with io.open(auth_conf_path, mode='w') as s:
        s.write(u"// This is Gate One's authentication settings file.\n")
        s.write(new_auth_settings)

# NOTE: After Gate One 1.2 is officially released this function will be removed:
def convert_old_server_conf():
    """
    Converts old-style server.conf files to the new settings/10server.conf
    format.
    """
    settings = RUDict()
    auth_settings = RUDict()
    terminal_settings = RUDict()
    api_keys = RUDict({"*": {"gateone": {"api_keys": {}}}})
    terminal_options = [ # These are now terminal-app-specific setttings
        'command', 'dtach', 'session_logging', 'session_logs_max_age',
        'syslog_session_logging'
    ]
    authentication_options = [
        # These are here only for logical separation in the .conf files
        'api_timestamp_window', 'auth', 'pam_realm', 'pam_service',
        'sso_realm', 'sso_service', 'ssl_auth'
    ]
    with io.open(options.config) as f:
        # Regular server-wide settings will go in 10server.conf by default.
        # These settings can actually be spread out into any number of .conf
        # files in the settings directory using whatever naming convention
        # you want.
        settings_path = options.settings_dir
        server_conf_path = os.path.join(settings_path, '10server.conf')
        # Using 20authentication.conf for authentication settings
        auth_conf_path = os.path.join(
            settings_path, '20authentication.conf')
        terminal_conf_path = os.path.join(settings_path, '50terminal.conf')
        api_keys_conf = os.path.join(settings_path, '30api_keys.conf')
        # NOTE: Using a separate file for authentication stuff for no other
        #       reason than it seems like a good idea.  Don't want one
        #       gigantic config file for everything (by default, anyway).
        logger.info(_(
            "Old server.conf file found.  Converting to the new format as "
            "%s, %s, and %s" % (
                server_conf_path, auth_conf_path, terminal_conf_path)))
        for line in f:
            if line.startswith('#'):
                continue
            key = line.split('=', 1)[0].strip()
            value = eval(line.split('=', 1)[1].strip())
            if key in terminal_options:
                if key == 'command':
                    # Fix the path to ssh_connect.py if present
                    if 'ssh_connect.py' in value:
                        value = value.replace(
                            '/plugins/', '/applications/terminal/plugins/')
                if key == 'session_logs_max_age':
                    # This is now user_logs_max_age.  Put it in 'gateone'
                    settings.update({'user_logs_max_age': value})
                terminal_settings.update({key: value})
            elif key in authentication_options:
                auth_settings.update({key: value})
            elif key == 'origins':
                # Convert to the new format (a list with no http://)
                origins = value.split(';')
                converted_origins = []
                for origin in origins:
                    # The new format doesn't bother with http:// or https://
                    if origin == '*':
                        converted_origins.append(origin)
                        continue
                    origin = origin.split('://')[1]
                    if origin not in converted_origins:
                        converted_origins.append(origin)
                settings.update({key: converted_origins})
            elif key == 'api_keys':
                # Move these to the new location/format (30api_keys.conf)
                for pair in value.split(','):
                    api_key, secret = pair.split(':')
                    if bytes == str:
                        api_key = api_key.decode('UTF-8')
                        secret = secret.decode('UTF-8')
                    api_keys['*']['gateone']['api_keys'].update(
                        {api_key: secret})
                # API keys can be written right away
                with io.open(api_keys_conf, 'w') as conf:
                    msg = _(
                        u"// This file contains the key and secret pairs "
                        u"used by Gate One's API authentication method.\n")
                    conf.write(msg)
                    conf.write(unicode(api_keys))
            else:
                settings.update({key: value})
        template_path = os.path.join(
            GATEONE_DIR, 'templates', 'settings', '10server.conf')
        new_settings = settings_template(template_path, settings=settings)
        if not os.path.exists(server_conf_path):
            with io.open(server_conf_path, 'w') as s:
                s.write(_(u"// This is Gate One's main settings file.\n"))
                s.write(new_settings)
        new_auth_settings = settings_template(
            template_path, settings=auth_settings)
        if not os.path.exists(auth_conf_path):
            with io.open(auth_conf_path, 'w') as s:
                s.write(_(
                    u"// This is Gate One's authentication settings file.\n"))
                s.write(new_auth_settings)
        # Terminal uses a slightly different template; it converts 'command'
        # to the new 'commands' format.
        template_path = os.path.join(
            GATEONE_DIR, 'templates', 'settings', '50terminal.conf')
        new_term_settings = settings_template(
            template_path, settings=terminal_settings)
        if not os.path.exists(terminal_conf_path):
            with io.open(terminal_conf_path, 'w') as s:
                s.write(_(
                    u"// This is Gate One's Terminal application settings "
                    u"file.\n"))
                s.write(new_term_settings)
    # Rename the old server.conf so this logic doesn't happen again
    os.rename(options.config, "%s.old" % options.config)

def apply_cli_overrides(go_settings):
    """
    Updates *go_settings* in-place with values given on the command line.
    """
    # Figure out which options are being overridden on the command line
    arguments = []
    non_options = [
        # These are things that don't really belong in settings
        'new_api_key', 'help', 'kill', 'config', 'combine_js', 'combine_css',
        'combine_css_container'
    ]
    for arg in list(sys.argv)[1:]:
        if not arg.startswith('-'):
            break
        else:
            arguments.append(arg.lstrip('-').split('=', 1)[0])
    go_settings['cli_overrides'] = arguments
    for argument in arguments:
        if argument in non_options:
            continue
        if argument in list(options):
            go_settings[argument] = options[argument]
    # Update Tornado's options from our settings.
    # NOTE: For options given on the command line this step should be redundant.
    for key, value in go_settings.items():
        if key in non_options:
            continue
        if key in list(options):
            if key in ('origins', 'api_keys'):
                # These two settings are special and taken care of elsewhere
                continue
            try:
                setattr(options, key, value)
            except Error:
                if isinstance(value, str):
                    if str == bytes: # Python 2
                        setattr(options, key, unicode(value))
                else:
                    setattr(options, key, str(value))

def remove_comments(json_like):
    """
    Removes C-style comments from *json_like* and returns the result.
    """
    def replacer(match):
        s = match.group(0)
        if s[0] == '/': return ""
        return s
    return comments_re.sub(replacer, json_like)

def remove_trailing_commas(json_like):
    """
    Removes trailing commas from *json_like* and returns the result.
    """
    return trailing_commas_re.sub("}", json_like)

def get_settings(path, add_default=True):
    """
    Reads any and all *.conf files containing JSON (JS-style comments are OK)
    inside *path* and returns them as an :class:`RUDict`.  Optionally, *path*
    may be a specific file (as opposed to just a directory).

    By default, all returned :class:`RUDict` objects will include a '*' dict
    which indicates "all users".  This behavior can be skipped by setting the
    *add_default* keyword argument to `False`.
    """
    settings = RUDict()
    if add_default:
        settings['*'] = {}
    # Using an RUDict so that subsequent .conf files can safely override
    # settings way down the chain without clobbering parent keys/dicts.
    if os.path.isdir(path):
        settings_files = [a for a in os.listdir(path) if a.endswith('.conf')]
        settings_files.sort()
    else:
        if not os.path.exists(path):
            raise IOError(_("%s does not exist" % path))
        settings_files = [path]
    for fname in settings_files:
        # Use this file to update settings
        if os.path.isdir(path):
            filepath = os.path.join(path, fname)
        else:
            filepath = path
        with io.open(filepath, encoding='utf-8') as f:
            # Remove comments
            almost_json = remove_comments(f.read())
            proper_json = remove_trailing_commas(almost_json)
            # Remove blank/empty lines
            proper_json = os.linesep.join([
                s for s in proper_json.splitlines() if s.strip()])
            try:
                settings.update(json_decode(proper_json))
            except ValueError as e:
                # Something was wrong with the JSON (syntax error, usually)
                logging.error(
                    "Error decoding JSON in settings file: %s"
                    % os.path.join(path, fname))
                logging.error(e)
                # Let's try to be as user-friendly as possible by pointing out
                # *precisely* where the error occurred (if possible)...
                try:
                    line_no = int(str(e).split(': line ', 1)[1].split()[0])
                    column = int(str(e).split(': line ', 1)[1].split()[2])
                    for i, line in enumerate(proper_json.splitlines()):
                        if i == line_no-1:
                            print(
                                line[:column] +
                                _(" <-- Something went wrong right here (or "
                                  "right above it)")
                            )
                            break
                        else:
                            print(line)
                    raise SettingsError()
                except (ValueError, IndexError):
                    print(_(
                        "Got an exception trying to display precisely where "
                        "the problem was.  This usually happens when you've "
                        "used single quotes (') instead of double quotes (\")."
                    ))
                    # Couldn't parse the exception message for line/column info
                    pass # No big deal; the user will figure it out eventually
    return settings

def options_to_settings(options):
    """
    Converts the given Tornado-style *options* to new-style settings.  Returns
    an :class:`RUDict` containing all the settings.
    """
    settings = RUDict({'*': {'gateone': {}, 'terminal': {}}})
    # In the new settings format some options have moved to the terminal app.
    # These settings are below and will be placed in the 'terminal' sub-dict.
    terminal_options = [
        'command', 'dtach', 'session_logging', 'session_logs_max_age',
        'syslog_session_logging'
    ]
    non_options = [
        # These are things that don't really belong in settings
        'new_api_key', 'help', 'kill', 'config'
    ]
    for key, value in options.items():
        if key in terminal_options:
            settings['*']['terminal'].update({key: value})
        elif key in non_options:
            continue
        else:
            if key == 'origins':
                #if value == '*':
                    #continue
                # Convert to the new format (a list with no http://)
                origins = value.split(';')
                converted_origins = []
                for origin in origins:
                    if '://' in origin:
                        # The new format doesn't bother with http:// or https://
                        origin = origin.split('://')[1]
                        if origin not in converted_origins:
                            converted_origins.append(origin)
                    elif origin not in converted_origins:
                        converted_origins.append(origin)
                settings['*']['gateone'].update({key: converted_origins})
            elif key == 'api_keys':
                if not value:
                    continue
                # API keys/secrets are now a dict instead of a string
                settings['*']['gateone']['api_keys'] = {}
                for pair in value.split(','):
                    api_key, secret = pair.split(':', 1)
                    if bytes == str: # Python 2
                        api_key = api_key.decode('UTF-8')
                        secret = secret.decode('UTF-8')
                    settings['*']['gateone']['api_keys'].update(
                        {api_key: secret})
            else:
                settings['*']['gateone'].update({key: value})
    return settings

def combine_javascript(path, settings_dir=None):
    """
    Combines all application and plugin .js files into one big one; saved to the
    given *path*.  If given, *settings_dir* will be used to determine which
    applications and plugins should be included in the dump based on what is
    enabled.
    """
    if not settings_dir:
        settings_dir = os.path.join(GATEONE_DIR, 'settings')
    all_settings = get_settings(settings_dir)
    enabled_plugins = []
    enabled_applications = []
    if 'gateone' in all_settings['*']:
        # The check above will fail in first-run situations
        enabled_plugins = all_settings['*']['gateone'].get(
            'enabled_plugins', [])
        enabled_applications = all_settings['*']['gateone'].get(
            'enabled_applications', [])
    plugins_dir = os.path.join(GATEONE_DIR, 'plugins')
    pluginslist = os.listdir(plugins_dir)
    pluginslist.sort()
    applications_dir = os.path.join(GATEONE_DIR, 'applications')
    appslist = os.listdir(applications_dir)
    appslist.sort()
    with io.open(path, 'w') as f:
        # Start by adding gateone.js
        gateone_js = os.path.join(GATEONE_DIR, 'static', 'gateone.js')
        with io.open(gateone_js) as go_js:
            f.write(go_js.read() + '\n')
        # Gate One plugins
        for plugin in pluginslist:
            if enabled_plugins and plugin not in enabled_plugins:
                continue
            static_dir = os.path.join(plugins_dir, plugin, 'static')
            if os.path.isdir(static_dir):
                filelist = os.listdir(static_dir)
                filelist.sort()
                for filename in filelist:
                    filepath = os.path.join(static_dir, filename)
                    if filename.endswith('.js'):
                        with io.open(filepath) as js_file:
                            f.write(js_file.read() + u'\n')
        # Gate One applications
        for application in appslist:
            if enabled_applications:
                # Only export JS of enabled apps
                if application not in enabled_applications:
                    continue
            static_dir = os.path.join(GATEONE_DIR,
                'applications', application, 'static')
            plugins_dir = os.path.join(
                applications_dir, application, 'plugins')
            if os.path.isdir(static_dir):
                filelist = os.listdir(static_dir)
                filelist.sort()
                for filename in filelist:
                    filepath = os.path.join(static_dir, filename)
                    if filename.endswith('.js'):
                        with io.open(filepath) as js_file:
                            f.write(js_file.read() + u'\n')
            app_settings = all_settings['*'].get(application, None)
            enabled_app_plugins = []
            if app_settings:
                enabled_app_plugins = app_settings.get(
                    'enabled_plugins', [])
            if os.path.isdir(plugins_dir):
                pluginslist = os.listdir(plugins_dir)
                pluginslist.sort()
                # Gate One application plugins
                for plugin in pluginslist:
                    # Only export JS of enabled app plugins
                    if enabled_app_plugins:
                        if plugin not in enabled_app_plugins:
                            continue
                    static_dir = os.path.join(plugins_dir, plugin, 'static')
                    if os.path.isdir(static_dir):
                        filelist = os.listdir(static_dir)
                        filelist.sort()
                        for filename in filelist:
                            filepath = os.path.join(static_dir, filename)
                            if filename.endswith('.js'):
                                with io.open(filepath) as js_file:
                                    f.write(js_file.read() + u'\n')
        f.flush()

def combine_css(path, container, settings_dir=None, log=True):
    """
    Combines all application and plugin .css template files into one big one;
    saved to the given *path*.  Templates will be rendered using the given
    *container* as the replacement for templates use of '#{{container}}'.

    If given, *settings_dir* will be used to determine which applications and
    plugins should be included in the dump based on what is enabled.

    If *log* is ``False`` messages indicating where the files
    have been saved will not be logged (useful when rendering CSS for
    programatic use).
    """
    if container.startswith('#'): # This is just in case (don't want ##gateone)
        container = container.lstrip('#')
    if not settings_dir:
        settings_dir = os.path.join(GATEONE_DIR, 'settings')
    all_settings = get_settings(settings_dir)
    enabled_plugins = []
    enabled_applications = []
    embedded = False
    url_prefix = '/'
    if 'gateone' in all_settings['*']:
        # The check above will fail in first-run situations
        enabled_plugins = all_settings['*']['gateone'].get(
            'enabled_plugins', [])
        enabled_applications = all_settings['*']['gateone'].get(
            'enabled_applications', [])
        embedded = all_settings['*']['gateone'].get('embedded', False)
        url_prefix = all_settings['*']['gateone'].get('url_prefix', False)
    plugins_dir = os.path.join(GATEONE_DIR, 'plugins')
    pluginslist = os.listdir(plugins_dir)
    pluginslist.sort()
    applications_dir = os.path.join(GATEONE_DIR, 'applications')
    appslist = os.listdir(applications_dir)
    appslist.sort()
    global_themes_dir = os.path.join(GATEONE_DIR, 'templates', 'themes')
    themes = os.listdir(global_themes_dir)
    theme_writers = {}
    for theme in themes:
        combined_theme_path = "%s_theme_%s" % (
            path.split('.css')[0], theme)
        theme_writers[theme] = io.open(combined_theme_path, 'w')
        themepath = os.path.join(global_themes_dir, theme)
        with io.open(themepath) as css_file:
            theme_writers[theme].write(css_file.read())
    # NOTE: We skip gateone.css because that isn't used when embedding
    with io.open(path, 'w') as f:
        # Gate One plugins
        # TODO: Add plugin theme files to this
        for plugin in pluginslist:
            if enabled_plugins and plugin not in enabled_plugins:
                continue
            css_dir = os.path.join(plugins_dir, plugin, 'templates')
            if os.path.isdir(css_dir):
                filelist = os.listdir(css_dir)
                filelist.sort()
                for filename in filelist:
                    filepath = os.path.join(css_dir, filename)
                    if filename.endswith('.css'):
                        with io.open(filepath) as css_file:
                            f.write(css_file.read() + u'\n')
        # Gate One applications
        for application in appslist:
            if enabled_applications:
                # Only export CSS of enabled apps
                if application not in enabled_applications:
                    continue
            css_dir = os.path.join(GATEONE_DIR,
                'applications', application, 'templates')
            subdirs = []
            plugins_dir = os.path.join(
                applications_dir, application, 'plugins')
            if os.path.isdir(css_dir):
                filelist = os.listdir(css_dir)
                filelist.sort()
                for filename in filelist:
                    filepath = os.path.join(css_dir, filename)
                    if filename.endswith('.css'):
                        with io.open(filepath) as css_file:
                            f.write(css_file.read() + u'\n')
                    elif os.path.isdir(filepath):
                        subdirs.append(filepath)
            while subdirs:
                subdir = subdirs.pop()
                filelist = os.listdir(subdir)
                filelist.sort()
                for filename in filelist:
                    filepath = os.path.join(subdir, filename)
                    if filename.endswith('.css'):
                        with io.open(filepath) as css_file:
                            combined = css_file.read() + u'\n'
                            if os.path.split(subdir)[1] == 'themes':
                                theme_writers[filename].write(combined)
                            else:
                                f.write(combined)
                    elif os.path.isdir(filepath):
                        subdirs.append(filepath)
            app_settings = all_settings['*'].get(application, None)
            enabled_app_plugins = []
            if app_settings:
                enabled_app_plugins = app_settings.get(
                    'enabled_plugins', [])
            if os.path.isdir(plugins_dir):
                pluginslist = os.listdir(plugins_dir)
                pluginslist.sort()
                # Gate One application plugins
                for plugin in pluginslist:
                    # Only export JS of enabled app plugins
                    if enabled_app_plugins:
                        if plugin not in enabled_app_plugins:
                            continue
                    css_dir = os.path.join(
                        plugins_dir, plugin, 'templates')
                    if os.path.isdir(css_dir):
                        filelist = os.listdir(css_dir)
                        filelist.sort()
                        for filename in filelist:
                            filepath = os.path.join(css_dir, filename)
                            if filename.endswith('.css'):
                                with io.open(filepath) as css_file:
                                    f.write(css_file.read() + u'\n')
                            elif os.path.isdir(os.path.join(
                                css_dir, filename)):
                                subdirs.append(filepath)
                    while subdirs:
                        subdir = subdirs.pop()
                        filelist = os.listdir(subdir)
                        filelist.sort()
                        for filename in filelist:
                            filepath = os.path.join(subdir, filename)
                            if filename.endswith('.css'):
                                with io.open(filepath) as css_file:
                                    with io.open(filepath) as css_file:
                                        combined = css_file.read() + u'\n'
                                        _dir = os.path.split(subdir)[1]
                                        if _dir == 'themes':
                                            theme_writers[filename].write(
                                                combined)
                                        else:
                                            f.write(combined)
                            elif os.path.isdir(filepath):
                                subdirs.append(filepath)
        f.flush()
    for writer in theme_writers.values():
        writer.flush()
        writer.close()
    # Now render the templates
    asis = lambda x: x # Used to disable autoescape
    import tornado.template
    loader = tornado.template.Loader(os.path.split(path)[0], autoescape="asis")
    template = loader.load(path)
    css_data = template.generate(
        asis=asis,
        container=container,
        url_prefix=url_prefix,
        embedded=embedded)
    # Overwrite it with the rendered version
    with io.open(path, 'wb') as f:
        f.write(css_data)
    if log:
        logging.info(_(
            "Non-theme CSS has been combined and saved to: %s" % path))
    for theme in theme_writers.keys():
        combined_theme_path = "%s_theme_%s" % (
            path.split('.css')[0], theme)
        template = loader.load(combined_theme_path)
        css_data = template.generate(
            asis=asis,
            container=container,
            url_prefix=url_prefix,
            embedded=embedded)
        with io.open(combined_theme_path, 'wb') as f:
            f.write(css_data)
        if log:
            logging.info(_(
                "The %s theme CSS has been combined and saved to: %s"
                % (theme.split('.css')[0], combined_theme_path)))
