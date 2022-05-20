"""
Core module

Core provides abstraction and plugins support to communicate with different source code control systems (sccs)
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

import importlib.util
import os
import sys
import glob

from devops_sccs.realtime.hookserver import HookServer
from .errors import PluginNotRegistered, PluginAlreadyRegistered
from .provision import Provision
from .context import Context
from .realtime.scheduler import Scheduler

# Built-in plugins
from .plugins import demo as plugin_demo


class Core(object):

    """
    Manages source code control systems
    Manages a standard workflow to use a specific source code control system
    """

    class ControlledContext:
        """Create/Delete context in a with statement"""

        def __init__(self, core, plugin_id, args):
            self.core = core
            self.plugin_id = plugin_id
            self.args = args

        async def __aenter__(self):
            self.ctx = await self.core.create_context(self.plugin_id, self.args)
            return self.ctx

        async def __aexit__(self, exc_type, exc, tb):
            await self.core.delete_context(self.ctx)

    def __init__(self):
        """Initialize plugins and internal modules"""

        self.plugins = {}
        self.scheduler = Scheduler()
        self.provision: Provision

    @classmethod
    async def create(cls, config=None):
        if config is None:
            config = {}
        self = Core()
        if config.get("provision") is not None:
            self.provision = Provision(
                config["provision"].get("checkout_base_path", "/tmp"),
                main=config["provision"]["main"],
                repository=config["provision"].get("repository", {}),
                templates=config["provision"].get("templates", {}),
            )

        # self.enableHook = config.get("hook_server") is not None
        # if self.enableHook:
        #     self.hookServer = HookServer(config["hook_server"])
        #     # we need to lock it up here , otherwise the endpoints won't register.
        #     await self.hookServer.lock.acquire()

        val = await self.load_builtin_plugins(config.get("plugins", {}))

        val = await self.load_external_plugins(config.get("plugins", {}))

        # if self.enableHook and val:
        #
        #     self.hookServer.lock.release()
        #     await self.hookServer.start_server()
        #
        return self

    async def cleanup(self):
        if self.provision is not None:
            self.provision.cleanup()

        # if self.enableHook:
        #     self.hookServer.stop_server()
        #
        for plugin_id in list(self.plugins.keys()):
            await self.unregister(plugin_id)

    async def load_builtin_plugins(self, plugins_config):
        """Built-in plugins

        A config can be passed to skip/or config some built-in plugins.
        By default all built-in plugins will be loaded
        """

        builtin = plugins_config.get("builtin", {})
        config = plugins_config.get("config", {})
        if builtin.get("demo", True):
            plugin_id, plugin = plugin_demo.init_plugin()
            await self.register(plugin_id, plugin, config.get("demo"))

        return True

    async def load_external_plugins(self, plugins_config):
        """External plugins

        All files with .py extension will be loaded
        """

        external_path = plugins_config.get("external")
        config = plugins_config.get("config", {})

        if os.path.isdir(external_path):
            sys.path.append(external_path)
            await self.register_plugins_in_folder(external_path, config)
        else:
            dev_external_path = f"{os.getcwd()}/plugins/devops_sccs"
            if os.path.isdir(dev_external_path):
                sys.path.append(dev_external_path)
                await self.register_plugins_in_folder(dev_external_path, config)

        return True

    async def register_plugins_in_folder(self, folder_path, config):
        for file in glob.glob(os.path.join(folder_path, "*.py")):
            spec = importlib.util.spec_from_file_location("", file)
            if spec is None:
                raise ModuleNotFoundError("something went wrong loading the plugin")
            mod = importlib.util.module_from_spec(spec)

            if spec.loader is not None:
                spec.loader.exec_module(mod)

            plugin_id, plugin = mod.init_plugin()
            await self.register(plugin_id, plugin, config.get(plugin_id))

    async def register(self, plugin_id, plugin, config):
        """Register a plugin to make it available"""
        if plugin_id in self.plugins:
            raise PluginAlreadyRegistered(plugin_id)

        await plugin.init(self, config)
        self.plugins[plugin_id] = plugin

    async def unregister(self, plugin_id):
        plugin = self.plugins.pop(plugin_id)
        await plugin.cleanup()

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

        return Context(session_id, session, plugin, self)

    async def delete_context(self, context, args=None):
        """Delete a context by closing a session"""

        await context.plugin.close_session(context.session_id, context.session, args)

    def context(self, plugin_id, args):
        """Controlled context to use in a with statement"""
        return Core.ControlledContext(self, plugin_id, args)
