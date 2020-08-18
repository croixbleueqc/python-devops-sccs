"""
Continuous Deployment Helper module
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

from ..errors import TriggerCdNotSupported, TriggerCdReadOnly, TriggerCdEnvUnsupported, TriggerCdVersionUnsupported, TriggerCdVersionAlreadyDeployed

def trigger_prepare(config, repository, environment, version):
    """
    Check conformity to trigger a deployment
    Extract involved configurations from the global config

    Args:
        config(typing.cd.Config): The configuration
        repository(str): the repository name
        environment(str): the environment (eg: production, development, qa, ...)
        version(str): version to deploy

    Returns:
        typing.cd.EnvironmentConfig, typing.cd.Available: Environment configuration target, Available Deployment target
    """
    env_config = config.get_environment_by_env(environment)
    available_config = config.get_available_by_version(version)

    if env_config is None:
        raise TriggerCdEnvUnsupported(repository, environment)

    if env_config.readonly:
        raise TriggerCdReadOnly(repository, environment)

    if available_config is None:
        raise TriggerCdVersionUnsupported(repository, version)

    if env_config.version == available_config.version:
        raise TriggerCdVersionAlreadyDeployed(repository, environment, version)
    
    return env_config, available_config

def trigger_not_supported(repository):
    """Trigger Continuous Delivery is not supported for this repository"""
    raise TriggerCdNotSupported(repository)
