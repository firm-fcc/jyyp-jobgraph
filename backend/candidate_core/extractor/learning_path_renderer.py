"""Deterministic renderer for frozen Learning Path Core results.

The planner owns every semantic decision.  This module only validates and
expresses the planner's structured output; it performs no graph selection,
proficiency inference, model call, or network access.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .learning_path_stage1 import (
    CapstoneEvidenceTask,
    CapstoneSpecializationExtension,
    GapItem,
    GapType,
    LearningPath,
    LearningPathResult,
    LearningStep,
    PathMode,
)


_EXPECTED_MODE = {
    GapType.MISSING: PathMode.LEARN,
    GapType.LEVEL_GAP: PathMode.DEEPEN,
    GapType.EVIDENCE_INSUFFICIENT: PathMode.VERIFY_FIRST,
    GapType.SATISFIED: PathMode.NONE,
}


@dataclass(frozen=True)
class RenderedLearningStep:
    node_id: str
    node_name: str
    reason: str
    evidence_task: str | None
    validation_criteria: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_name": self.node_name,
            "reason": self.reason,
            "evidence_task": self.evidence_task,
            "validation_criteria": list(self.validation_criteria),
        }


@dataclass(frozen=True)
class RenderedVerificationGuidance:
    task_id: str
    task_name: str
    task_description: str | None
    validation_criteria: tuple[str, ...]
    source_references: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "task_description": self.task_description,
            "validation_criteria": list(self.validation_criteria),
            "source_references": list(self.source_references),
        }


@dataclass(frozen=True)
class RenderedSpecializationExtension:
    subskill_id: str
    task_description: str
    validation_criteria: tuple[str, ...]

    @classmethod
    def from_planner(
        cls,
        value: CapstoneSpecializationExtension,
    ) -> "RenderedSpecializationExtension":
        return cls(
            subskill_id=value.subskill_id,
            task_description=value.task_description,
            validation_criteria=value.validation_criteria,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "subskill_id": self.subskill_id,
            "task_description": self.task_description,
            "validation_criteria": list(self.validation_criteria),
        }


@dataclass(frozen=True)
class RenderedCapstoneGuidance:
    task_id: str
    objective: str
    task_description: str
    specialization_extensions: tuple[RenderedSpecializationExtension, ...]
    validation_criteria: tuple[str, ...]
    purpose: str

    @classmethod
    def from_planner(cls, value: CapstoneEvidenceTask) -> "RenderedCapstoneGuidance":
        return cls(
            task_id=value.task_id,
            objective=value.objective,
            task_description=value.task_description,
            specialization_extensions=tuple(
                RenderedSpecializationExtension.from_planner(extension)
                for extension in value.specialization_extensions
            ),
            validation_criteria=value.validation_criteria,
            purpose=value.purpose,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "objective": self.objective,
            "task_description": self.task_description,
            "specialization_extensions": [
                extension.to_dict() for extension in self.specialization_extensions
            ],
            "validation_criteria": list(self.validation_criteria),
            "purpose": self.purpose,
        }


@dataclass(frozen=True)
class RenderedSkillPath:
    team_skill_id: str
    team_skill_name: str
    gap_type: str
    observed_level: str | None
    required_level: str | None
    path_mode: str
    achieved_node_ids: tuple[str, ...]
    current_state: str
    gap_explanation: str
    development_goal: str
    learning_steps: tuple[RenderedLearningStep, ...]
    specialization_extensions: tuple[RenderedSpecializationExtension, ...]
    verification_guidance: RenderedVerificationGuidance | None
    capstone_guidance: RenderedCapstoneGuidance | None
    reassessment_required: bool
    reassessment_guidance: str | None
    path_status: str
    render_status: str = "READY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_skill_id": self.team_skill_id,
            "team_skill_name": self.team_skill_name,
            "gap_type": self.gap_type,
            "observed_level": self.observed_level,
            "required_level": self.required_level,
            "path_mode": self.path_mode,
            "achieved_node_ids": list(self.achieved_node_ids),
            "current_state": self.current_state,
            "gap_explanation": self.gap_explanation,
            "development_goal": self.development_goal,
            "learning_steps": [step.to_dict() for step in self.learning_steps],
            "specialization_extensions": [
                extension.to_dict() for extension in self.specialization_extensions
            ],
            "verification_guidance": (
                self.verification_guidance.to_dict() if self.verification_guidance else None
            ),
            "capstone_guidance": (
                self.capstone_guidance.to_dict() if self.capstone_guidance else None
            ),
            "reassessment_required": self.reassessment_required,
            "reassessment_guidance": self.reassessment_guidance,
            "path_status": self.path_status,
            "render_status": self.render_status,
        }


@dataclass(frozen=True)
class RenderedLearningPathResult:
    candidate_id: str
    target_job_id: str
    skill_paths: tuple[RenderedSkillPath, ...]
    render_status: str = "READY"

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "target_job_id": self.target_job_id,
            "skill_paths": [path.to_dict() for path in self.skill_paths],
            "render_status": self.render_status,
        }

    def to_canonical_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


class LearningPathRenderer:
    """Render frozen planner results without changing their decisions."""

    def render(
        self,
        result: LearningPathResult,
    ) -> RenderedLearningPathResult:
        if len(result.gap_items) != len(result.paths):
            raise ValueError("planner gap_items and paths must have equal length")

        rendered: list[RenderedSkillPath] = []
        for gap_item, path in zip(result.gap_items, result.paths):
            rendered.append(self._render_skill(gap_item, path))
        return RenderedLearningPathResult(
            candidate_id=result.candidate_id,
            target_job_id=result.target_job_id,
            skill_paths=tuple(rendered),
        )

    def _render_skill(
        self,
        gap_item: GapItem,
        path: LearningPath,
    ) -> RenderedSkillPath:
        self._validate_planner_contract(gap_item, path)

        verification_guidance: RenderedVerificationGuidance | None = None
        learning_steps: tuple[RenderedLearningStep, ...] = ()
        if path.mode is PathMode.VERIFY_FIRST:
            verification_guidance = self._render_verification(path.ordered_steps[0])
        elif path.mode in {PathMode.LEARN, PathMode.DEEPEN}:
            learning_steps = tuple(
                RenderedLearningStep(
                    node_id=step.subskill_id,
                    node_name=step.name_zh,
                    reason=(
                        f"该节点由冻结规划器按 {path.mode.value} 模式选择；"
                        "保持既定顺序完成并留存行为证据。"
                    ),
                    evidence_task=step.evidence_task,
                    validation_criteria=step.validation_criteria,
                )
                for step in path.ordered_steps
            )

        capstone_guidance = (
            RenderedCapstoneGuidance.from_planner(path.capstone_evidence_task)
            if path.capstone_evidence_task is not None
            else None
        )
        specialization_extensions = (
            capstone_guidance.specialization_extensions if capstone_guidance else ()
        )
        current_state, development_goal = self._state_text(gap_item)
        return RenderedSkillPath(
            team_skill_id=gap_item.team_skill_id,
            team_skill_name=gap_item.team_skill_name,
            gap_type=gap_item.gap_type.value,
            observed_level=gap_item.observed_level,
            required_level=gap_item.required_level,
            path_mode=path.mode.value,
            achieved_node_ids=tuple(
                achieved.subskill_id for achieved in path.achieved_subskills
            ),
            current_state=current_state,
            gap_explanation=gap_item.explanation,
            development_goal=development_goal,
            learning_steps=learning_steps,
            specialization_extensions=specialization_extensions,
            verification_guidance=verification_guidance,
            capstone_guidance=capstone_guidance,
            reassessment_required=path.reassessment_required,
            reassessment_guidance=self._reassessment_text(path),
            path_status=path.path_status,
        )

    @staticmethod
    def _validate_planner_contract(
        gap_item: GapItem,
        path: LearningPath,
    ) -> None:
        if gap_item.team_skill_id != path.team_skill_id:
            raise ValueError("planner gap/path team_skill_id mismatch")
        expected_mode = _EXPECTED_MODE[gap_item.gap_type]
        if path.mode is not expected_mode:
            raise ValueError(
                f"planner gap/path mode mismatch for {gap_item.team_skill_id}: "
                f"{gap_item.gap_type.value}/{path.mode.value}"
            )
        if gap_item.gap_type is GapType.LEVEL_GAP and (
            gap_item.observed_level is None or gap_item.required_level is None
        ):
            raise ValueError("LEVEL_GAP must preserve observed and required proficiency levels")
        expected_reassessment = gap_item.gap_type is not GapType.SATISFIED
        if path.reassessment_required is not expected_reassessment:
            raise ValueError("planner gap/reassessment semantics mismatch")
        node_ids = tuple(step.subskill_id for step in path.ordered_steps)
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("planner learning node IDs must be unique")
        if path.mode is PathMode.VERIFY_FIRST:
            if len(path.ordered_steps) != 1:
                raise ValueError("VERIFY_FIRST must contain exactly one planner verification task")
            if path.ordered_steps[0].action_mode is not PathMode.VERIFY_FIRST:
                raise ValueError("VERIFY_FIRST task action_mode mismatch")
            if path.capstone_evidence_task is not None:
                raise ValueError("VERIFY_FIRST must not contain a capstone")
        elif path.mode is PathMode.NONE:
            if path.ordered_steps or path.capstone_evidence_task is not None:
                raise ValueError("NONE must not contain learning nodes or capstone")
            if path.reassessment_required:
                raise ValueError("NONE must not require reassessment")
        else:
            if any(step.action_mode is not path.mode for step in path.ordered_steps):
                raise ValueError("planner learning step action_mode mismatch")

    @staticmethod
    def _render_verification(step: LearningStep) -> RenderedVerificationGuidance:
        return RenderedVerificationGuidance(
            task_id=step.subskill_id,
            task_name=step.name_zh,
            task_description=step.evidence_task,
            validation_criteria=step.validation_criteria,
            source_references=step.source_references,
        )

    @staticmethod
    def _state_text(item: GapItem) -> tuple[str, str]:
        if item.gap_type is GapType.MISSING:
            return (
                "当前未发现足够行为证据支持该能力。",
                "按照冻结规划器给出的 LEARN 路径形成新的、可验证的行为证据。",
            )
        if item.gap_type is GapType.LEVEL_GAP:
            return (
                "已有行为证据支持该能力，但当前观察熟练度"
                f" {item.observed_level} 尚低于岗位要求 {item.required_level}。",
                "按照冻结规划器给出的 DEEPEN 路径扩展已观察能力范围并形成新证据。",
            )
        if item.gap_type is GapType.EVIDENCE_INSUFFICIENT:
            return (
                "现有证据不足以可靠判断当前熟练度；这不表示能力水平低。",
                "先完成规划器返回的 VERIFY_FIRST 任务，补充可归因证据后再评估。",
            )
        return (
            "当前证据下，该能力已满足目标岗位要求，无需进入当前优先学习路径。",
            "保持当前证据记录；本次不生成额外学习节点。",
        )

    @staticmethod
    def _reassessment_text(path: LearningPath) -> str | None:
        if not path.reassessment_required:
            return None
        if path.mode is PathMode.VERIFY_FIRST:
            return (
                "验证任务只用于生成新的行为证据并重新评估当前熟练度；"
                "它不表示低水平，也不会直接产生 P1/P2/P3/P4。"
            )
        return (
            "完成学习步骤或 capstone 只能生成新的行为证据；"
            "熟练度必须由后续评估重新判定，不会自动升级。"
        )
