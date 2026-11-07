"""Request and response models."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator

_NAME = re.compile(r"^[a-z0-9-]{1,32}$")
_LOCATION = re.compile(r"^[a-z0-9]{1,32}$")


class CreateCluster(BaseModel):
    name: str
    location: str = "eastus"

    @field_validator("name")
    @classmethod
    def name_ok(cls, value: str) -> str:
        if not _NAME.match(value):
            raise ValueError("name must be 1-32 characters of lowercase letters, digits, or hyphen")
        return value

    @field_validator("location")
    @classmethod
    def location_ok(cls, value: str) -> str:
        if not _LOCATION.match(value):
            raise ValueError("location must be an Azure region name")
        return value


class DeleteCluster(BaseModel):
    confirm: str
    delete_backups: bool = False


class RestoreBody(BaseModel):
    backup_id: str
    confirm: str


class OperationOut(BaseModel):
    id: str
    type: str
    status: str
    step: str | None = None
    error_message: str | None = None
    updated_at: str | None = None


class NodeOut(BaseModel):
    id: str
    ordinal: int
    role: str
    status: str
    private_ip: str | None = None
    public_ip: str | None = None
    vm_name: str | None = None


class CqlContact(BaseModel):
    host: str
    port: int = 9042
    username: str
    password: str | None = None
    keyspace: str = "demo"


class ClusterOut(BaseModel):
    id: str
    name: str
    status: str
    location: str
    grafana_url: str | None = None
    io_profile: str | None = None
    manager_install: str | None = None
    nodes: list[NodeOut] = Field(default_factory=list)
    cql: CqlContact | None = None
    latest_backup: dict | None = None
    operation: OperationOut | None = None


class HealthNode(BaseModel):
    id: str
    ordinal: int
    status: str
    private_ip: str | None = None


class HealthOut(BaseModel):
    cluster_status: str
    automation: str
    ops_status: str
    nodes: list[HealthNode]
