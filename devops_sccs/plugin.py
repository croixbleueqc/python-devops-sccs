"""
Plugin module

Abstract implementation of what a plugin look like

IMPORTANT: If you consider that some features are enough generic and can help other,
           be free to submit them in the Core as an helper to build plugins.

           Generic plugins are welcome in the Core too.
"""

# Copyright 2019 mickybart
# Copyright 2020-2022 Croix Bleue du Québec

# This file is part of python-devops-sccs.

# python-devops-sccs is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# python-devops-sccs is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.

# You should have received a copy of the GNU Lesser General Public License
# along with python-devops-sccs.  If not, see <https://www.gnu.org/licenses/>.


def init_plugin():
    """
    Entrypoint to register a plugin.

    id has to be unique. You can't register 2 plugins with the same id

    Returns:
        str,object: unique id, plugin instance
    """
    return "sccs", Sccs()


class Sccs(object):
    """
    Abstract class to create a plugin
    """

    async def init(self, core, args):
        """
        Initialize the plugin

        This step is run one time juster after the init_plugin() call.
        This is where you can initialize everything that is not relative to a session.

        Advanced examples:
        - if you want to share some sessions across different instances, this is where you can init database, cache or whatever you want.
        - if you need a kind of root user to do some specific operations not available to a regular user,
          this is where you can store those informations to use them when required

        Args:
            core(Core)    : Core library
            args(dict)    : static configuration for the plugin
        """
        raise NotImplementedError()

    async def cleanup(self):
        """
        Cleanup the plugin as it will be removed
        """
        raise NotImplementedError()

    def get_session_id(self, args):
        """
        Permit to generate the same session id for the same significant arguments.

        session id is an abstract concept that can be used for advanced usages explained on open_session().
        It is totally acceptable to return None if you don't need it as the Core will not keep any trace of it.
        This is a purely internal plugin use.

        Args:
            args(dict): extra configuration

        Returns:
            str: a session id
        """
        raise NotImplementedError()

    async def open_session(self, session_id, args):
        """
        Open a session

        Session is an abstract concept that will be stored in a Core.Context. This is up to the plugin to define what should be a session.

        A session can be :
        - nothing (None) if you want to use a global user (see root user usage in init())
        - object that will identify a regular user (provided with args) to run as much as possible commands against your sccs with effective user credential
        - object like a library client instance on your specific sccs.
        - ...

        Advanced example:
        - check if a session is alreday open in a cache/database/... system initialized for this plugin
        - if the session exists, return it instead of opening a new one
        - if it doesn't exist, open a new one and store it in the cache/database/...

        Simple example:
        - just return args as the session. Args should include everything that will permit all other functions to query the sccs.

        Extra easy example with a "root user":
        - return None: we don't need anything as we will rely only on the root user / connection created in init() stage (for auditability and security you should not do that)

        Args:
            args(dict): extra arguments required to open a session

        Returns:
            object|None: a session object
        """
        raise NotImplementedError()

    async def close_session(self, session_id, session, args):
        """Close a session

        Args:
            session(object): the session
            args(dict): extr arguments to handle the operation
        """
        raise NotImplementedError()

    async def accesscontrol(self, session, repository, action, args):
        """Access Control

        Control if the action can be done for this repository on this session

        Args:
            session(object): the session
            repository(dict): Answers to a repository contract
            action(admission.Actions): Action requested
            args(dict): extr arguments to handle the operation

        Exceptions:
            AccessForbidden: Access forbidden
        """
        raise NotImplementedError()

    async def passthrough(self, session, request, args):
        """Passthrough

        Permit to support non standard operations. That can be see like a way to add proprietary APIs.

        Args:
            session(object): the session
            request(str): the non standard request to perform
            args(dict): extr arguments to handle the request

        Returns:
            object: non standard answer
        """
        raise NotImplementedError()

    async def get_repositories(self, session, args):
        """Get a list of repositories (with permission for each)

        This list can be restricted to what is visible only based on requester's permissions.

        Args:
            session(object): the session
            args(dict): extr arguments to handle the operation

        Returns:
            list(typing.repositories.Repository): List of repository
        """
        raise NotImplementedError()

    async def get_repository(self, session, repository, args):
        """Get a specific repository (with permission)

        Args:
            session(object): the session
            repository(str): the repository name
            args(dict): extr arguments to handle the operation

        Returns:
            typing.repositories.Repository: a repository

        """
        raise NotImplementedError()

    async def add_repository(
        self, session, provision, repository, template, template_params, args
    ):
        """Add a new repository

        The main workflow is:
        - Check user permissions to add a new repository
        - Prepare (verify answers for selected template with repository's name); provision.prepare_provision
        - Verify if the repository doesn't exist and create it
        - Store the repository definition somewhere (for tracability, remediation, scheduled validation ...)
        - Add the new repository (provide git credentials to do it, main branch ...); provision.provision
        - Enforce security (permissions, branches strategy...)
        - Return instruction to use the new repository

        Args:
            session(object): the session
            provision(Provision): the provision class (provides helpers, templates, etc)
            repository(dict): Answers to a repository contract
            template(str): Template to use
            template_params(dict): Answers to a template contract
            args(dict): extr arguments to handle the operation
        """
        raise NotImplementedError()

    async def get_continuous_deployment_config(
        self, session, repository, environments, args
    ):
        """Get continuous deployment configuration

        This is not the real state of the deployment in your "production" environment but the state expected
        based on git status

        Args:
            session(object): the session
            repository(str): the repository name
            environments(list(str)): filter to those environments only (None for all)
            args(dict): extr arguments to handle the operation

        Returns:
            list(typing.cd.EnvironmentConfig): Configuration for filtered environments
        """
        raise NotImplementedError()

    async def get_continuous_deployment_versions_available(
        self, session, repository, args
    ):
        """Get continuous deployment versions available

        This is a list of versions that can be used to trigger a continuous deployment

        Args:
            session(object): the session
            repository(str): the repository name
            args(dict): extr arguments to handle the operation

        Returns:
            list(typing.cd.Available): Versions available
        """
        raise NotImplementedError()

    async def trigger_continuous_deployment(
        self, session, repository, environment, version, args
    ):
        """Trigger a continuous deployment

        Args:
            session(object): the session
            repository(str): the repository name
            environment(str): the environment (eg: production, development, qa, ...)
            version(str): version to deploy
            args(dict): extr arguments to handle the operation

        Returns:
            typing.cd.EnvironmentConfig: new configuration for the environment
        """
        raise NotImplementedError()

    async def get_continuous_deployment_environments_available(
        self, session, repository, args
    ):
        """List all environments that can be used to run the application

        Args:
            session(object): the session
            repository(str): the repository name
            args(dict): extr arguments to handle the operation

        Returns:
            list(typing.cd.EnvironmentConfig): list of environments
        """
        raise NotImplementedError()

    async def bridge_repository_to_namespace(
        self, session, repository, environment, untrustable, args
    ):
        """Bridge repository/environment to a kubernetes namespace

        EXPERIMENTAL FEATURE

        The bridge permit to provide the namespace associated with a repository. This API is really experimental because it assumes
        that a repository equal one namepace. This is a wrong assumption and will need to be reviewed.

        This function will be called in an untrustable way by the kubernetes backend. Kubernetes backend will fully rely on sccs to validate the request.

        Return dict object:
        {
            "cluster": <kubernetes cluster name usable by python-devops-kubernetes>,
            "namespace": "",
            "repository": {
                "write_access": <True|False>
            }
        }

        Args:
            session(object): the session
            repository(str): the repository name
            environment(str): the environment (eg: production, development, qa, ...)
            unstrustable(bool): used to enforce controls on the plugin to distinguish a critical request from the kubernetes backend
            args(dict): extr arguments to handle the operation

        Returns:
            dict: a bridge object
        """
        raise NotImplementedError()

    async def compliance(self, session, remediation, report, args):
        """Check if all repositories are compliants

        No remediation should be done by default if a repository is not compliant.
        A remediation can failed if manual intervention is required.
        An optional report can be send back to the requester

        The report should be cached/stored to be provided in an efficient way with compliance_report

        Args:
            session(object): the session
            remediation(bool): force a remediation
            report(bool): send a report (avoid to call compliance_report)
            args(dict): extr arguments to handle the operation

        Returns:
            dict|None: an optional report
        """
        raise NotImplementedError()

    async def compliance_report(self, session, args):
        """Provides a compliance report about all repositories

        Returns:
            dict: a compliance report for all repositories
        """
        raise NotImplementedError()

    async def compliance_repository(
        self, session, repository, remediation, report, args
    ):
        """Check if a repository is compliant

        No remediation should be done by default if a repository is not compliant.
        A remediation can failed if manual intervention is required.
        An optional report can be send back to the requester

        The report should be cached/stored to be provided in an efficient way with compliance_report_repository

        Args:
            session(object): the session
            repository(str): the repository name
            remediation(bool): force a remediation
            report(bool): send a report (avoid to call compliance_report_repository)
            args(dict): extr arguments to handle the operation

        Returns:
            dict|None: an optional report
        """
        raise NotImplementedError()

    async def compliance_report_repository(self, session, repository, args):
        """Provides a compliance report for the repository

        Returns:
            dict: a compliance report for the repository
        """
        raise NotImplementedError()

    async def get_hooks_repository(self, session, repository, args):
        """Check if a repository is compliant

        No remediation should be done by default if a repository is not compliant.
        A remediation can failed if manual intervention is required.
        An optional report can be send back to the requester

        The report should be cached/stored to be provided in an efficient way with compliance_report_repository

        Args:
            session(object): the session
            repository(str): the repository name
            remediation(bool): force a remediation
            report(bool): send a report (avoid to call compliance_report_repository)
            args(dict): extr arguments to handle the operation

        Returns:
            dict|None: an optional report
        """
