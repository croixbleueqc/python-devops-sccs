"""
Core module

Core provides abstraction and plugins support to communicate with different source code control systems (sccs)
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

import importlib.util
import os
import glob
import logging

from .errors import PluginNotRegistered, PluginAlreadyRegistered
from .provision import Provision

# Built-in plugins
from .plugins import demo as plugin_demo

class Core(object):
    """
    Manages source code control systems
    Manages a standard workflow to use a specific source code control system
    """

    class Context(object):
        """
        Context permits to communicate with a source code control system for a specific session.
        A session is an abstract concept that can hold nothing or any object type understandable by the plugin which issued it.
        """
        def __init__(self, session, plugin, core):
            self.session = session
            self.plugin = plugin
            self._core = core
        
        async def passthrough(self, request, args=None):
            """Passthough

            see plugin.py for function description
            """
            return await self.plugin.passthrough(self.session, request, args)
        
        async def get_repositories(self, args=None):
            """Get a list of repositories
            
            see plugin.py for function description
            """
            return await self.plugin.get_repositories(self.session, args)

        async def get_repository_permissions(self, repository, args=None):
            """Get permissions for a specific repository
            
            see plugin.py for function description
            """
            return await self.plugin.get_repository_permissions(self.session, repository, args)

        async def get_all_repositories_permissions(self, args=None):
            """Get permisions for all accessible repositories
            
            see plugin.py for function description
            """
            return await self.plugin.get_all_repositories_permissions(self.session, args)

        # async def create_repository(self, args):
        #     """Create and provision a new repository

        #     see plugin.py for function description
        #     """

        #     return await self.plugin.create_repository(self.session, args, self._core.provision)

    def __init__(self, config={}):
        """Initialize plugins and internal modules"""
        self.plugins = {}

        self.load_builtin_plugins(config.get("plugins", {}))

        self.load_external_plugins(config.get("plugins", {}))

        # TODO: refactor the provision feature. see plugin.py / create_repository() description
        self.provision = Provision(
            config.get("checkout_base_path", "/tmp/devops/provision"),
            templates=config.get("templates", {})
            )

    def load_builtin_plugins(self, plugins_config):
        """Built-in plugins
        
        A config can be passed to skip/or config some built-in plugins.
        By default all built-in plugins will be loaded
        """

        builtin = plugins_config.get("builtin", {})
        config = plugins_config.get("config", {})

        if builtin.get("demo", True):
            plugin_id, plugin = plugin_demo.init_plugin()
            self.register(plugin_id, plugin, config.get("demo"))

    def load_external_plugins(self, plugins_config):
        """External plugins
        
        All files with .py extension will be loaded
        """

        external_path = plugins_config.get("external")

        if external_path is None:
            return

        config = plugins_config.get("config", {})
        
        for file in glob.glob(os.path.join(external_path, "*.py")):
            spec = importlib.util.spec_from_file_location("", file)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            plugin_id, plugin = mod.init_plugin()
            self.register(plugin_id, plugin, config.get(plugin_id))

    def register(self, plugin_id, plugin, config):
        """Register a plugin to make it available"""
        if plugin_id in self.plugins:
            raise PluginAlreadyRegistered(plugin_id)

        plugin.init(config)
        self.plugins[plugin_id] = plugin

    async def create_context(self, plugin_id, args):
        """Create a context
        
        A session and the real plugin will be embedded in a context.

        This is the main entry point to communicate with the sccs
        """
        plugin = self.plugins.get(plugin_id)
        if plugin is None:
            raise PluginNotRegistered(plugin_id)
        
        session_id = plugin.get_session_id(args)

        session = await plugin.open_session(session_id, args)

        return Core.Context(session, plugin, self)

    async def delete_context(self, context, args=None):
        """Delete a context by closing a session"""

        await context.plugin.close_session(
            context.session, args)
