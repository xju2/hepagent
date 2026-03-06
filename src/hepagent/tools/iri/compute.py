import json
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from agents import RunContextWrapper, function_tool
from hepagent.agents.common import AgentContext
from hepagent.tools.iri.iri_config import IRI_ACCESS_TOKEN_KEY_NAME, IRI_RESOURCE_ID_KEY_NAME


# https://exaworks.org/psij-python/docs/v/0.9.11/.generated/index.html#psij.resource_spec.ResourceSpec
class ResourceSpec(BaseModel):
    node_count: int = Field(description="Number of nodes to use for the job")
    processes_per_node: int = Field(description="Number of tasks to run on each node")
    cpu_cores_per_process: int = Field(description="Number of CPU cores for each task")
    gpu_count: int = Field(description="Number of GPUs per task")
    exclusive_node_use: bool = Field(
        description="Whether to request exclusive access to nodes", default=True
    )


# https://exaworks.org/psij-python/docs/v/0.9.11/.generated/index.html#jobattributes
class AttributeCustom(BaseModel):
    key: str = Field(description="Name of the custom attribute")
    value: str = Field(description="Value of the custom attribute")


class JobAttributes(BaseModel):
    duration: int = Field(description="Expected duration of the job in seconds")
    queue_name: str = Field(description="Name of the queue or partition to submit the job to")
    account: str = Field(description="Account or project to charge for the job")
    custom_attributes: list[AttributeCustom] | None = Field(
        description="Additional job attributes as key/value pairs", default=None
    )


class JobSpecs(BaseModel):
    executable: str = Field(description="Path to the executable to run")
    arguments: list[str] | None = Field(description="List of command-line arguments.", default=None)
    directory: str = Field(description="Working directory for the job", default=".")
    name: str = Field(description="Name of the job", default="iri_test_job")
    inherit_environment: bool = Field(
        description="Whether to inherit the current environment variables", default=True
    )
    stdout_path: str | Path | None = Field(description="Path to save standard output", default=None)
    stderr_path: str | Path | None = Field(description="Path to save standard error", default=None)
    resources: ResourceSpec = Field(description="Resource requirements for the job")
    attributes: JobAttributes = Field(description="Job attributes are details about the job.")
    post_launch: str | Path | None = Field(
        description="An optional path to a post-launch script", default=None
    )
    launcher: str | None = Field(
        description="Launcher to use for the job (e.g. srun, aprun, jsrun)", default="srun"
    )


def _call_operation_json(
    client,
    operation_id: str,
    *,
    path_params: dict[str, object] | None = None,
    query: dict[str, object] | None = None,
    body: dict[str, object] | None = None,
) -> dict[str, object]:
    payload = client.call_operation(
        operation_id,
        path_params_json=json.dumps(path_params) if path_params else None,
        query_json=json.dumps(query) if query else None,
        body_json=json.dumps(body) if body else None,
    )
    return json.loads(payload)


def _job_payload(job_specs: JobSpecs) -> dict[str, object]:
    payload = job_specs.model_dump(exclude_none=True)
    attributes = payload.get("attributes")
    if isinstance(attributes, dict):
        custom_attrs = attributes.get("custom_attributes")
        if isinstance(custom_attrs, list):
            attr_map = {
                attr["key"]: attr["value"]
                for attr in custom_attrs
                if attr.get("key") and attr.get("value") is not None
            }
            if attr_map:
                attributes["custom_attributes"] = attr_map
            else:
                attributes.pop("custom_attributes", None)
        elif not custom_attrs:
            attributes.pop("custom_attributes", None)
    return payload


@function_tool
def submit_job(ctx: RunContextWrapper[AgentContext], job_specs: JobSpecs) -> str:
    """
    Submits a job to American Science Cloud (AmSC) using the
    Integrated Research Infrastructure (IRI) API.
    The job specifications are provided as a JobSpecs object.

    Args:
        job_specs: A JobSpecs object containing all necessary information to submit the job.

    Returns:
        str: A message indicating the result of the job submission,
        including the job ID if successful.
    """
    from iri_client import Client

    load_dotenv()
    access_token = os.getenv(IRI_ACCESS_TOKEN_KEY_NAME)
    base_url = "https://api.iri.nersc.gov"

    client = Client(base_url=base_url, access_token=access_token)

    resource_id = os.getenv(IRI_RESOURCE_ID_KEY_NAME, "b3af92a7-cf5f-42cf-a4be-6f6554a779e3")

    created_job = _call_operation_json(
        client,
        "launchJob",
        path_params={"resource_id": resource_id},
        body=_job_payload(job_specs),
    )

    job_id = created_job.get("id")
    if not isinstance(job_id, str) or not job_id:
        raise RuntimeError(f"launchJob did not return a valid job id: {created_job}")

    return f"Job submitted successfully with ID: {job_id}"


@function_tool
def get_job_status(ctx: RunContextWrapper[AgentContext], job_id: str) -> str:
    """
    Retrieves the status of a job submitted to AmSC via the IRI API.

    Args:
        job_id: The ID of the job to check.

    Returns:
        str: A message indicating the current status of the job.
    """
    from iri_client import Client

    load_dotenv()
    access_token = os.getenv(IRI_ACCESS_TOKEN_KEY_NAME)
    base_url = "https://api.iri.nersc.gov"

    client = Client(base_url=base_url, access_token=access_token)

    resource_id = os.getenv(IRI_RESOURCE_ID_KEY_NAME, "b3af92a7-cf5f-42cf-a4be-6f6554a779e3")

    job_status = _call_operation_json(
        client,
        "getJob",
        path_params={"resource_id": resource_id, "job_id": job_id},
    )

    status = job_status.get("status")
    if isinstance(status, dict):
        state = status.get("state", "unknown").strip().lower()
    else:
        state = "unknown"

    return f"Current status of job {job_id}: {state}"
