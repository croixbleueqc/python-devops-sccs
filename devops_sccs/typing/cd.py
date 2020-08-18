"""
Continuous Deployment Typing

Define standard typing to manage continuous deployment
"""

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

from typing_engine.typing import Typing2, Field

class EnvironmentConfig(Typing2):
    """
    Defines which version is deployed on a specific environment.
    
    readonly field permit to know if we can trigger a continuous deployment for this environment
    """
    environment = Field()
    version = Field()
    readonly = Field(instanciator=bool, default=False)

class Available(Typing2):
    """
    Defines an available deployment (typically generated from a pipeline) with a build number and an associated version.
    """
    build = Field()
    version = Field()

class Config(Typing2):
    """
    Continuous Deployment Configuration
    """
    availables = Field().list_of(Available)
    environments = Field().list_of(EnvironmentConfig)

    def get_environment_by_env(self, env):
        for i in self.environments:
            if i.environment == env:
                return i
        
        return None

    def get_available_by_version(self, version):
        for i in self.availables:
            if i.version == version:
                return i
        
        return None
