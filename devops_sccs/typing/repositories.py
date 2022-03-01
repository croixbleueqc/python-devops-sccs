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
from . import WatcherTyping2

class Repository(WatcherTyping2):
    name = Field()
    permission = Field()

    def __eq__(self, other):
        if not isinstance(other, Repository):
            return False

        return self.name == other.name
