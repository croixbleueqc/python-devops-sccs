"""
Event Typing

Event triggered by the realtime module
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

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventType(str, Enum):
    ADDED = "ADDED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"
    INFO = "INFO"


class Event(BaseModel):  # AKA dataResponse
    key: int
    type: EventType = Field(alias="_type")
    value: Any

    class Config:
        json_encoders = {EventType: lambda t: str(t)}
