from typing import Any

from pydantic import BaseModel, Field, Extra, EmailStr


class From(BaseModel):
    git: str = Field(
        ...,
        regex=r"(^git@bitbucket\.org:croixbleue/[a-zA-Z0-9-_]+\.git$|^https://"
              r"[w]{0,3}\.?github.com/croixbleueqc/[a-zA-Z0-9-_]+(.git)?$)",
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


class TemplateSetup(BaseModel):
    cmd: list[str] | None
    args: dict[str, Arg] | None


class Template(BaseModel):
    from_: From = Field(alias="from")
    setup: TemplateSetup


class WatcherCreds(BaseModel):
    user: str
    pwd: str
    email: EmailStr | None = None


class Environment(BaseModel):
    name: str
    branch: str
    version: dict[str, str]
    trigger: dict[str, bool] = {}


class PullRequest(BaseModel):
    tag: str


class Pipeline(BaseModel):
    versions_available: list[str]


class ContinuousDeployment(BaseModel):
    environments: list[Environment]
    pullrequest: PullRequest
    pipeline: Pipeline


class Storage(BaseModel):
    path: str
    git: str
    repo: str


class EscalationDetails(BaseModel):
    repository: str
    permissions: list[str]


class PluginConfig(BaseModel, extra=Extra.allow):
    team: str
    watcher: WatcherCreds
    continuous_deployment: ContinuousDeployment
    storage: Storage
    escalation: dict[str, EscalationDetails]


class Plugins(BaseModel):
    external: str
    builtin: dict[str, bool]
    config: dict[str, PluginConfig]


class MainContract(BaseModel):
    repository_validator: str
    template_required: bool


class RepoContractProjectValue(BaseModel):
    name: str
    key: str


class RepoContractValue(BaseModel, extra=Extra.allow):
    type: str
    description: str
    required: bool


class RepoContractProject(RepoContractValue):
    roleName: str
    values: list[RepoContractProjectValue]


class RepoContractConfigValue(BaseModel):
    short: str
    key: str


class RepoContractConfig(RepoContractValue):
    default: int
    roleName: str
    values: list[RepoContractConfigValue]


class RepoContractPrivileges(RepoContractValue):
    roleName: str
    values: list[RepoContractConfigValue]


class RepositoryContract(BaseModel):
    project: RepoContractProject
    configuration: RepoContractConfig
    privileges: RepoContractPrivileges


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
