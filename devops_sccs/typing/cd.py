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

from typing_engine.typing import Field
from enum import Enum
from . import WatcherTyping2

class BuildStatusType(Enum):
    SUCCESSFUL = "SUCCESSFUL"
    FAILED ="FAILED"
    INPROGRESS = "INPROGRESS"
    def __str__(self):
        return self.value

class EnvironmentConfig(WatcherTyping2):
    """
    Defines which version is deployed on a specific environment.
    
    readonly field permit to know if we can trigger a continuous deployment for this environment
    
    """
    environment = Field()
    version = Field()
    readonly = Field(instanciator=bool, default=False)
    pullrequest = Field()
    

    def __eq__(self, other):
        if not isinstance(other, EnvironmentConfig):
            return False
        
        return self.environment == other.environment and \
                self.version == other.version and \
                self.readonly == other.readonly and \
                self.pullrequest == other.pullrequest

    def __hash__(self):
        return hash((
            self.environment,
            self.version,
            self.readonly,
            self.pullrequest
        ))

class Available(WatcherTyping2):
    """
    Defines an available deployment (typically generated from a pipeline) with a build number and an associated version.
    """
    build = Field()
    version = Field()
    _buildstatus = Field(instanciator=BuildStatusType).converter(dumps=str).mapping("buildstatus")

    def __eq__(self, other):
        if not isinstance(other, Available):
            return False
        
        return self.build == other.build and \
                self.version == other.version

    def __hash__(self):
        return hash((
            self.build,
            self.version
        ))
