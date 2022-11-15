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

import glob
import importlib.util
import logging
import os
import sys
from contextlib import asynccontextmanager

from .context import Context
from .errors import PluginAlreadyRegistered, PluginNotRegistered
from .plugin import SccsApi
from .provision import Provision
from .realtime.scheduler import Scheduler
from .redis import RedisCache
from .schemas.config import Plugins, SccsConfig
from .typing.credentials import Credentials

# runtime plugins stare
plugins: dict[str, SccsApi] = {}


def register_plugin(name, plugin):
    if name in plugins:
        raise PluginAlreadyRegistered(name)
    plugins[name] = plugin


class SccsClient(object):
    """
    Manages source code control systems
    Manages a standard workflow to use a specific source code control system
    """

    def __init__(self):
        """Initialize plugins and internal modules"""
        self.scheduler = Scheduler()
        self.provision: Provision

    @classmethod
    async def create(cls, config: SccsConfig):
        self = SccsClient()

        # Initialize the cache
        try:
            RedisCache().init()
        except Exception:
            logging.error("Unable to initialize Redis cache. Exiting...")
            sys.exit(1)

        if config.provision is not None:
            self.provision = Provision(config=config.provision)

        plugins_config = config.plugins
        await self.load_builtin_plugins(plugins_config)

        await self.load_external_plugins(plugins_config)

        return self

    @asynccontextmanager
    async def context(self, plugin_id: str, credentials: Credentials | dict[str, str] | None):
        global plugins

        plugin = plugins.get(plugin_id)
        if plugin is None:
            raise PluginNotRegistered(plugin_id)

        if isinstance(credentials, dict):
            credentials = Credentials(**credentials)

        session_id = plugin.get_session_id(credentials)

        s = await plugin.open_session(session_id, credentials)

        ctx = Context(session_id, s.session, plugin, self)

        try:
            yield ctx
        finally:
            await plugin.close_session(session_id)

    async def cleanup(self):
        if self.provision is not None:
            self.provision.cleanup()

        global plugins
        for plugin_id in list(plugins.keys()):
            await self.unregister(plugin_id)

    async def load_builtin_plugins(self, plugins_config: Plugins):
        """Built-in plugins

        A config can be passed to skip/or config some built-in plugins.
        By default all built-in plugins will be loaded
        """

        # builtin = plugins_config.get("builtin", {})
        # config = plugins_config.get("config", {})
        # todo?

    async def load_external_plugins(self, plugins_config: Plugins):
        """External plugins

        All files with .py extension will be loaded
        """

        external_path = plugins_config.external
        config = plugins_config.config

        if os.path.isdir(external_path):
            sys.path.append(external_path)
            await self.register_plugins_in_folder(external_path, config)
        else:
            dev_external_path = f"{os.getcwd()}/app/plugins/devops_sccs"
            if os.path.isdir(dev_external_path):
                sys.path.append(dev_external_path)
                await self.register_plugins_in_folder(dev_external_path, config)

    async def register_plugins_in_folder(self, folder_path, config):
        for file in glob.glob(os.path.join(folder_path, "*.py")):
            spec = importlib.util.spec_from_file_location("", file)
            if spec is None:
                raise ModuleNotFoundError("something went wrong loading the plugin")
            mod = importlib.util.module_from_spec(spec)

            if spec.loader is not None:
                spec.loader.exec_module(mod)

            await self.init_plugin(mod.PLUGIN_NAME, config.get(mod.PLUGIN_NAME, {}))
            # await self.register(plugin_id, plugin, config.get(plugin_id))

    async def init_plugin(self, plugin_name, config):
        """Register a plugin to make it available"""
        global plugins
        if plugin_name not in plugins:
            raise PluginNotRegistered(plugin_name)

        await plugins[plugin_name].init(self, config)

    async def unregister(self, plugin_id):
        global plugins
        try:
            await plugins.pop(plugin_id).cleanup()
        except KeyError:
            logging.warning(f"Plugin {plugin_id} not registered")
