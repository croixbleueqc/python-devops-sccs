from typing import Any

from pydantic import BaseModel, Field


class From(BaseModel):
    git: str = Field(
        ...,
        regex=r"(^git@bitbucket\.org:croixbleue/[a-zA-Z0-9-_]+\.git$|^https://[w]{0,3}\.?github.com/croixbleueqc/[a-zA-Z0-9-_]+(.git)?$)",
    )
    main_branch: str
    other_branches: list[str] = []


class Arg(BaseModel):
    type: str
    description: str
    required: bool = False
    default: None | bool
    validator: str | None
    arg: str | dict[str, Any]


class Setup(BaseModel):
    cmd: list[str] | None
    args: dict[str, Arg] | None


class Template(BaseModel):
    from_: From = Field(alias="from")
    setup: Setup


class Plugins(BaseModel):
    external: str
    builtin: dict[str, bool]
    config: dict[str, Any]


class MainContract(BaseModel):
    repository_validator: str
    template_required: bool


class ProjectValue(BaseModel):
    name: str
    key: str


class Project(BaseModel):
    type: str
    description: str
    required: bool
    roleName: str
    values: list[ProjectValue]


class ConfigurationValue(BaseModel):
    short: str
    key: str


class Configuration(BaseModel):
    type: str
    description: str
    required: bool
    default: int
    roleName: str
    values: list[ConfigurationValue]


class Privileges(BaseModel):
    type: str
    description: str
    required: bool
    roleName: str
    values: list[ConfigurationValue]


class RepositoryContract(BaseModel):
    project: Project
    configuration: Configuration
    privileges: Privileges


class ProvisionConfig(BaseModel):
    checkout_base_path: str
    main_contract: MainContract = Field(alias="main")
    repository_contract: RepositoryContract = Field(alias="repository")
    templates: dict[str, Template]


class HookServer(BaseModel):
    host: str
    port: int


class SccsConfig(BaseModel):
    plugins: Plugins
    provision: ProvisionConfig
    hook_server: HookServer
