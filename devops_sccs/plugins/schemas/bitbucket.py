from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Generic, Literal, Optional, TypeVar

from pydantic import UUID4, AnyHttpUrl, BaseModel, Extra, Field
from pydantic.generics import GenericModel


class BitbucketResource(BaseModel, extra=Extra.allow):
    """Base type for most resource objects. It defines the common type element that identifies an object's type"""

    type: str = ""


class Link(BaseModel):
    name: str | None
    href: AnyHttpUrl


class Account(BitbucketResource):
    """An account object"""

    links: dict[str, Link] = {}
    username: str | None = Field(regex=r"^[a-zA-Z0-9_\-]+$")
    nickname: str = ""
    account_status = "active"
    display_name: str = ""
    website: str = ""
    created_on: datetime | None
    uuid: UUID4 | None
    has_2fa_enabled: bool | None


class Author(BitbucketResource):
    raw: str
    user: Account


class Participant(BitbucketResource):
    user: Account
    role: Literal["PARTICIPANT", "REVIEWER"]
    approved: bool
    state: Literal["approved", "changes_requested", "null"]
    participated_on: datetime


class Project(BitbucketResource):
    links: dict[str, Link] = {}
    uuid: UUID4 | None
    key: str
    owner: Account | None
    name: str | None
    description: str = ""
    is_private: bool = True
    created_on: datetime | None
    updated_on: datetime | None
    has_publicly_visible_repos: bool = False


class BaseCommit(BitbucketResource):
    hash: str = Field(regex=r"^[0-9a-f]{7,}?$")
    date: datetime | None
    author: Author | None
    message: str = ""
    summary: BitbucketResource | None
    parents: "list[BaseCommit]" = []


class Commit(BitbucketResource):
    repository: Optional["Repository"]
    participants: list[Participant] = []


class Ref(BitbucketResource):
    links: dict[str, Link] = {}
    name: str
    target: BaseCommit | Commit


class BranchMatchKind(str, Enum):
    GLOB = "glob"
    BRANCHING_MODEL = "branching_model"


class MergeStrategy(str, Enum):
    MERGE_COMMIT = "merge_commit"
    SQUASH = "squash"
    FAST_FORWARD = "fast_forward"


class ForkPolicy(str, Enum):
    ALLOW_FORKS = "allow_forks"
    NO_PUBLIC_FORKS = "no_public_forks"
    NO_FORKS = "no_forks"


class Permission(str, Enum):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


class GroupPermission(BaseModel):
    permission: Permission
    group: str | dict[str, str]

    @property
    def slug(self):
        if isinstance(self.group, dict):
            return self.group["slug"]
        return self.group


class GroupPermissions(BaseModel):
    permissions: list[GroupPermission] = []


class DeployKey(BaseModel):
    id: str | None = None
    key: str
    label: str = ""


class Branch(BitbucketResource):
    merge_strategies: list[MergeStrategy] = []
    default_merge_strategy: MergeStrategy


class Repository(BitbucketResource):
    """A Bitbucket repository"""

    links: dict[str, Link]
    uuid: UUID4
    full_name: str
    is_private: bool
    parent: "Optional[Repository]" = None
    scm = "git"
    owner: Account
    name: str
    created_on: datetime
    updated_on: datetime
    size: int
    language: str
    has_issues: bool
    has_wiki: bool
    fork_policy: Literal["allow_forks", "no_public_forks", "no_forks"]
    project: Project
    mainbranch: Ref | Branch


class ProjectValue(BaseModel):
    name: str
    key: str


class ConfigOrPrivilegeValue(BaseModel):
    short: str
    key: str


class RepositoryPost(BaseModel, extra=Extra.allow):
    """Payload for creating a repository"""

    name: str
    description: str = ""
    project: ProjectValue
    configuration: ConfigOrPrivilegeValue
    privileges: ConfigOrPrivilegeValue
    scm = "git"


R = TypeVar("R", bound=BitbucketResource)


class Paginated(GenericModel, Generic[R]):
    """A paginated object"""

    size: int
    page: int
    pagelen: int
    next: Optional[AnyHttpUrl]
    previous: Optional[AnyHttpUrl]
    values: list[R]


class RepositoryPut(RepositoryPost):
    """Payload for updating a repository"""

    pass


class PaginatedRepositories(BaseModel):
    """A paginated list of repositories"""

    size: int = Field(ge=0)
    page: int = Field(ge=1)
    pagelen: int = Field(ge=1)
    next: AnyHttpUrl | None
    previous: AnyHttpUrl | None
    values: list[Repository]


BaseCommit.update_forward_refs()
Commit.update_forward_refs()
Repository.update_forward_refs()
