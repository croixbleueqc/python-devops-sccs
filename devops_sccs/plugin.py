"""
Plugin module

Abstract implementation of what a plugin look like

IMPORTANT: If you consider that some features are enough generic and can help other,
           be free to submit them in the Core as an helper to build plugins.

           Generic plugins are welcome in the Core too.
"""

# Copyright 2019 mickybart
# Copyright 2020 Croix Bleue du Québec

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

    def init(self, args):
        """
        Initialize the plugin

        This step is run one time juster after the init_plugin() call.
        This is where you can initialize everything that is not relative to a session.

        Advanced examples:
        - if you want to share some sessions across different instances, this is where you can init database, cache or whatever you want.
        - if you need a kind of root user to do some specific operations not available to a regular user,
          this is where you can store those informations to use them when required
        
        Args:
            args(dict): static configuration for the plugin
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

    async def close_session(self, session, args):
        """Close a session
        
        Args:
            session(object): the session
            args(dict): extr arguments to handle the operation
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
        """Get a list of repositories

        This list can be restricted to what is visible only based on requester's permissions.

        Args:
            session(object): the session
            args(dict): extr arguments to handle the operation
        
        Returns:
            list(object): TODO: define object in a better way to have an abstraction of what a repo should look like
        """
        raise NotImplementedError()

    # TODO: need some refactoring / just kept as a demo for now
    # async def create_repository(self, session, args, provision):
    #     """Create a repository
        
    #     The main workflow is:
    #     - validate args (can be partially done with provision.is_payload_valid)
    #     - create the repository
    #     - provision a template (can use provision.execute)
    #     """
    #     raise NotImplementedError()

    async def get_repository_permissions(self, session, repository, args):
        """Get permissions for a specific repository

        Args should at least contains the repository name.
        
        Args:
            session(object): the session 
            repository(str): the repository name
            args(dict): extr arguments to handle the operation
        
        Returns:
            object: TODO: define object in a better way to have an abstraction of what a permission should look like

        """
        raise NotImplementedError()

    async def get_all_repositories_permissions(self, session, args):
        """Get permisions for all accessible repositories
        
        This list can be restricted to what is visible only based on requester's permissions.
        
        Args:
            session(object): the session
            args(dict): extr arguments to handle the operation
        
        Returns:
            list(object): TODO: define object in a better way to have an abstraction of what a permission should look like
        """
        raise NotImplementedError()
