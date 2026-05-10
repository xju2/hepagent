"""Explorer agent for broad research-direction discovery."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from agents import Agent, Runner, function_tool
from hepagent.model_providers import get_model_provider


@dataclass(frozen=True)
class SpecialistConfig:
    """Configuration for a research specialist sub-agent."""

    key: str
    name: str
    description: str
    instructions: str


SPECIALISTS: tuple[SpecialistConfig, ...] = (
    SpecialistConfig(
        key="collider",
        name="Collider Physics Specialist",
        description="Collider phenomenology, detectors, and LHC-style measurements.",
        instructions=(
            "You are a collider physics specialist. Evaluate the user's question from "
            "the perspective of accelerator experiments, detector signatures, analysis "
            "strategies, systematic uncertainties, and near-term phenomenology."
        ),
    ),
    SpecialistConfig(
        key="cosmology",
        name="Cosmology Specialist",
        description="Large-scale structure, CMB, simulations, and astro-cosmology probes.",
        instructions=(
            "You are a cosmology specialist. Evaluate the user's question from the "
            "perspective of large-scale structure, CMB, simulations, survey data, "
            "theory systematics, and connections to astrophysical observables."
        ),
    ),
    SpecialistConfig(
        key="neutrino",
        name="Neutrino Physics Specialist",
        description="Neutrino oscillations, mass, sources, and rare-event experiments.",
        instructions=(
            "You are a neutrino physics specialist. Evaluate the user's question from "
            "the perspective of oscillation physics, neutrino mass, source modeling, "
            "rare-event detection, backgrounds, and experimental reach."
        ),
    ),
    SpecialistConfig(
        key="theory",
        name="Theory Specialist",
        description="Formal theory, model building, EFT, and cross-domain mechanisms.",
        instructions=(
            "You are a high-energy theory specialist. Evaluate the user's question "
            "from the perspective of model building, effective field theory, "
            "symmetries, calculability, and falsifiable predictions."
        ),
    ),
    SpecialistConfig(
        key="instrumentation",
        name="Instrumentation Specialist",
        description="Detector concepts, readout, controls, computing, and analysis tooling.",
        instructions=(
            "You are an instrumentation and scientific-computing specialist. Evaluate "
            "the user's question from the perspective of detector design, readout, "
            "triggering, calibration, data systems, analysis software, and feasibility."
        ),
    ),
)


SPECIALIST_KEYS = {specialist.key for specialist in SPECIALISTS}


def _parse_specialist_keys(specialist_keys: str) -> list[str]:
    requested = [
        key.strip().lower() for key in specialist_keys.replace(";", ",").split(",") if key.strip()
    ]
    return requested


def _select_specialists(specialist_keys: str = "") -> list[SpecialistConfig]:
    """Select requested specialists, falling back to the default broad panel."""

    requested = _parse_specialist_keys(specialist_keys)
    if not requested:
        return list(SPECIALISTS[:3])

    selected = [specialist for specialist in SPECIALISTS if specialist.key in requested]
    unknown = sorted(set(requested) - SPECIALIST_KEYS)
    if unknown:
        valid = ", ".join(sorted(SPECIALIST_KEYS))
        raise ValueError(f"Unknown specialist key(s): {', '.join(unknown)}. Valid keys: {valid}")
    return selected


def _specialist_prompt(question: str, interests: str, specialist: SpecialistConfig) -> str:
    interest_context = f"\nUser interests or constraints: {interests}" if interests.strip() else ""
    return (
        f"Research question: {question}{interest_context}\n\n"
        "Provide a concise specialist memo with these sections:\n"
        "- Most promising research directions\n"
        "- Useful methods, datasets, or tools\n"
        "- Key risks, prerequisites, or failure modes\n"
        "- Concrete first steps for a researcher entering the area\n"
        "- Cross-field connections worth exploring\n"
    )


async def _run_specialist(
    specialist: SpecialistConfig,
    question: str,
    interests: str,
    *,
    model_provider: str,
    model_name: str | None,
) -> tuple[str, str]:
    agent = Agent(
        name=specialist.name,
        instructions=specialist.instructions,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[],
    )
    result = await Runner.run(agent, _specialist_prompt(question, interests, specialist))
    return specialist.name, str(result.final_output)


async def run_specialist_panel(
    question: str,
    interests: str = "",
    specialist_keys: str = "",
    *,
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> str:
    """Run selected specialist agents concurrently and format their findings."""

    specialists = _select_specialists(specialist_keys)
    results = await asyncio.gather(
        *[
            _run_specialist(
                specialist,
                question,
                interests,
                model_provider=model_provider,
                model_name=model_name,
            )
            for specialist in specialists
        ]
    )
    sections = [
        "# Parallel specialist findings",
        f"Question: {question}",
        "Specialists: " + ", ".join(name for name, _ in results),
    ]
    for name, output in results:
        sections.append(f"\n## {name}\n{output}")
    return "\n".join(sections)


def _create_exploration_tool(model_provider: str, model_name: str | None):
    @function_tool(
        name_override="explore_research_directions",
        description_override=(
            "Run multiple specialist research agents in parallel and return their "
            "findings for synthesis. specialist_keys is optional comma-separated "
            "text using keys: collider, cosmology, neutrino, theory, instrumentation."
        ),
    )
    async def explore_research_directions(
        question: str,
        interests: str = "",
        specialist_keys: str = "",
    ) -> str:
        """Explore a research question through parallel specialist agents."""

        return await run_specialist_panel(
            question,
            interests=interests,
            specialist_keys=specialist_keys,
            model_provider=model_provider,
            model_name=model_name,
        )

    return explore_research_directions


EXPLORER_INSTRUCTIONS = """You are the HepAgent Explorer.

Your purpose is to help researchers map unfamiliar or cross-disciplinary HEP and
cosmology research directions. For substantive research-exploration questions,
call explore_research_directions before answering so collider, cosmology,
neutrino, theory, or instrumentation perspectives can be considered in parallel.

After receiving specialist findings, synthesize them into a researcher-facing
answer. Prioritize:
- promising directions and why they matter,
- concrete first steps,
- relevant methods, data, tools, or collaborations,
- prerequisites and risks,
- new cross-field directions suggested by the specialist panel.

Be explicit when a recommendation is speculative. Keep final answers concise,
structured, and useful for a researcher deciding what to investigate next.
"""


def create(
    model_provider: str = "cborg",
    model_name: str | None = None,
) -> Agent:
    """Create the Explorer agent."""

    agent = Agent(
        name="Explorer Agent",
        instructions=EXPLORER_INSTRUCTIONS,
        model=get_model_provider(model_provider=model_provider, model_name=model_name),
        tools=[_create_exploration_tool(model_provider, model_name)],
    )
    return agent
