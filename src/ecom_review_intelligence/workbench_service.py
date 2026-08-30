"""Synthetic public-demo service behind the P7 HTTP boundary."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from ecom_review_intelligence.agent_contracts import (
    AgentClaim,
    AgentDraft,
    AgentPlan,
    AgentRequest,
    ClaimType,
    FollowUpActionCode,
    LimitationCode,
    SafeEvidenceCard,
    Workflow,
)
from ecom_review_intelligence.agent_graph import (
    AgentGraphDependencies,
    open_sqlite_agent_graph,
)
from ecom_review_intelligence.agent_tools import (
    EvidenceToolService,
    ProductResolver,
)
from ecom_review_intelligence.agent_trace import append_local_trace, serialize_public_trace
from ecom_review_intelligence.engine import query_aspect, verify_citation
from ecom_review_intelligence.model_gateway import RequestBudget
from ecom_review_intelligence.workbench_contracts import (
    ClarificationView,
    ComparisonRow,
    EvidenceDetail,
    EvidenceRowView,
    EvidenceView,
    ProductView,
    TriageItem,
    WorkbenchResumeRequest,
    WorkbenchRun,
    WorkbenchRunRequest,
)


class WorkbenchInputError(ValueError):
    pass


class WorkbenchRunNotFound(LookupError):
    pass


class WorkbenchRunConflict(RuntimeError):
    pass


class WorkbenchEvidenceUnavailable(LookupError):
    """Raised when a run has no on-demand review-text detail to serve."""


class WorkbenchService(Protocol):
    mode: str

    def create_run(self, request: WorkbenchRunRequest) -> WorkbenchRun: ...

    def get_run(self, run_token: str) -> WorkbenchRun: ...

    def resume_run(self, run_token: str, request: WorkbenchResumeRequest) -> WorkbenchRun: ...


_PRODUCTS = {
    "frostline": ProductView(
        code="frostline", label="Frostline Countertop", segment="Countertop ice maker"
    ),
    "northstar": ProductView(
        code="northstar", label="Northstar Compact", segment="Compact ice maker"
    ),
    "harbor": ProductView(code="harbor", label="Harbor Station", segment="Countertop ice maker"),
}
_LOCAL_PRODUCTS = {
    "frostline": ProductView(
        code="frostline", label="受控本地商品 A", segment="私有目录别名"
    ),
    "northstar": ProductView(
        code="northstar", label="受控本地商品 B", segment="私有目录别名"
    ),
    "harbor": ProductView(
        code="harbor", label="受控本地商品 C", segment="私有目录别名"
    ),
}

_ASPECT_WORDING = {
    "ice_quality": ("制冰质量", "核查冰体一致性与形状"),
    "noise": ("运行噪音", "核查间歇性运行噪音"),
    "reliability": ("可靠性", "复盘反复出现的可靠性信号"),
    "cleaning_maintenance": ("清洁维护", "复盘清洁维护阻力"),
    "capacity_speed": ("产能与速度", "核查出冰产能与单轮速度"),
    "ease_setup": ("安装体验", "复盘安装指引"),
    "leakage": ("漏水", "调查用户报告的漏水情形"),
    "size_portability": ("尺寸与便携", "核查摆放与便携约束"),
}


def _run_alias(token: str) -> str:
    return f"RUN-{UUID(token).hex[:8].upper()}"


class SyntheticWorkbenchService:
    """Deterministic synthetic mode used for public pages, screenshots, and demos."""

    def __init__(self, uuid_factory: Callable[[], UUID | str] = uuid4) -> None:
        self.mode = "synthetic"
        self._uuid_factory = uuid_factory
        self._runs: dict[str, WorkbenchRun] = {}
        self._pending: dict[str, WorkbenchRunRequest] = {}

    def create_run(self, request: WorkbenchRunRequest) -> WorkbenchRun:
        token = str(self._uuid_factory())
        if not request.product_codes:
            choices = (self._product_view("frostline"), self._product_view("harbor"))
            run = WorkbenchRun(
                run_token=token,
                run_alias=_run_alias(token),
                status="CLARIFICATION_REQUIRED",
                workflow=request.workflow,
                title="需要选择商品",
                summary="两个合成目录条目匹配本次请求。",
                evidence_count=0,
                degraded=False,
                next_action="选择一个商品以继续同一运行。",
                clarification=ClarificationView(
                    prompt="应诊断哪个目录条目?", choices=choices
                ),
            )
            self._pending[token] = request
        else:
            run = self._build_result(token, request, request.product_codes)
        self._runs[token] = run
        return run

    def _product_view(self, code: str) -> ProductView:
        return _PRODUCTS[code]

    def get_run(self, run_token: str) -> WorkbenchRun:
        try:
            return self._runs[run_token]
        except KeyError as error:
            raise WorkbenchRunNotFound from error

    def resume_run(self, run_token: str, request: WorkbenchResumeRequest) -> WorkbenchRun:
        if run_token not in self._runs:
            raise WorkbenchRunNotFound
        original = self._pending.get(run_token)
        if original is None:
            raise WorkbenchRunConflict
        allowed = {item.code for item in self._runs[run_token].clarification.choices}
        if set(request.selected_product_codes) - allowed:
            raise WorkbenchInputError("Selection is not authorized for this run.")
        count = len(request.selected_product_codes)
        invalid_shape = (
            original.workflow is Workflow.DIAGNOSIS
            and count != 1
            or original.workflow is Workflow.COMPARISON
            and count != 2
            or original.workflow is Workflow.TRIAGE
            and not 1 <= count <= 5
        )
        if invalid_shape:
            raise WorkbenchInputError("Selection does not match the workflow.")
        self._pending.pop(run_token)
        run = self._build_result(run_token, original, request.selected_product_codes)
        self._runs[run_token] = run
        return run

    def _build_result(
        self, token: str, request: WorkbenchRunRequest, product_codes: tuple[str, ...]
    ) -> WorkbenchRun:
        try:
            products = tuple(self._product_view(code) for code in product_codes)
        except KeyError as error:
            raise WorkbenchInputError("Unknown synthetic product.") from error
        aspect_label, primary_issue = _ASPECT_WORDING[request.aspect.value]
        evidence = tuple(
            EvidenceView(
                evidence_alias=f"EV-{index:02d}",
                product_code=product.code,
                aspect=request.aspect,
                polarity="opposing" if index % 2 else "conflicting",
                strength="STRONG" if index == 1 else "MODERATE",
                excerpt=(
                    f"合成评论信号提到{aspect_label}。"
                    if index == 1
                    else f"合成反馈显示{aspect_label}体验不一。"
                ),
            )
            for index, product in enumerate(products, start=1)
        )
        comparison_rows: tuple[ComparisonRow, ...] = ()
        triage_items: tuple[TriageItem, ...] = ()
        if request.workflow is Workflow.COMPARISON:
            comparison_rows = tuple(
                ComparisonRow(
                    product_code=product.code,
                    signal=f"{aspect_label}评论信号",
                    direction="watch" if index == 1 else "mixed",
                    strength="STRONG" if index == 1 else "MODERATE",
                    evidence_count=1,
                    comparable=True,
                    insufficient_reason=None,
                )
                for index, product in enumerate(products, start=1)
            )
        elif request.workflow is Workflow.TRIAGE:
            triage_items = (
                TriageItem(
                    rank=1,
                    issue=primary_issue,
                    priority="high",
                    owner="Quality",
                    evidence_count=len(evidence),
                ),
                TriageItem(
                    rank=2,
                    issue=f"确认{aspect_label}处理指引",
                    priority="medium",
                    owner="Customer support",
                    evidence_count=len(evidence),
                ),
            )
        return WorkbenchRun(
            run_token=token,
            run_alias=_run_alias(token),
            status="VERIFIED_RESULT",
            workflow=request.workflow,
            title={
                Workflow.DIAGNOSIS: "SKU 证据诊断",
                Workflow.COMPARISON: "竞品证据比较",
                Workflow.TRIAGE: "运营问题分诊",
            }[request.workflow],
            summary=(
                "合成证据已就绪,供运营复核。"
                "信号描述评论内容,不代表已验证的缺陷率。"
            ),
            evidence_count=len(evidence),
            degraded=False,
            next_action="复核证据别名并确认下一步内部核查。",
            products=products,
            evidence=evidence,
            comparison_rows=comparison_rows,
            triage_items=triage_items,
        )


class _DeterministicWorkbenchGateway:
    def __init__(self, workflow: Workflow, aspect: str) -> None:
        self.workflow = workflow
        self.aspect = aspect

    def plan(self, sanitized_question: str) -> AgentPlan:
        del sanitized_question
        return AgentPlan(
            workflow=self.workflow,
            product_terms=("CONTROLLED_PRODUCT",),
            aspects=(self.aspect,),
        )

    def draft(
        self,
        plan: AgentPlan,
        cards: tuple[SafeEvidenceCard, ...],
        *,
        validation_errors: tuple[str, ...] = (),
    ) -> AgentDraft:
        del validation_errors
        claims = []
        for card in cards:
            if card.returned_evidence_count == 0:
                polarity = "insufficient"
                references: tuple[str, ...] = ()
                claim_type = ClaimType.INSUFFICIENT_EVIDENCE
            else:
                polarity = next(
                    value
                    for value, count in (
                        ("opposing", card.opposing_count),
                        ("conflicting", card.conflicting_count),
                        ("supporting", card.supporting_count),
                    )
                    if count > 0
                )
                references = (card.card_alias,)
                claim_type = (
                    ClaimType.VALIDATION_PRIORITY
                    if plan.workflow is Workflow.TRIAGE
                    else ClaimType.ASPECT_SUMMARY
                )
            claims.append(
                AgentClaim(
                    claim_type=claim_type,
                    product_aliases=(card.product_alias,),
                    aspect=card.aspect,
                    polarity=polarity,
                    evidence_strength=card.evidence_strength,
                    safe_evidence_card_refs=references,
                )
            )
        return AgentDraft(
            workflow=plan.workflow,
            selected_product_aliases=plan.product_terms,
            selected_aspects=plan.aspects,
            claims=tuple(claims),
            follow_up_action_codes=(FollowUpActionCode.VALIDATE_LOCALLY,),
            limitation_codes=(LimitationCode.REVIEW_EVIDENCE_ONLY,),
        )


class P6BackedWorkbenchService(SyntheticWorkbenchService):
    """Controlled local adapter that reuses the P6 graph and SQLite evidence tool."""

    def __init__(
        self,
        *,
        store_path: Path,
        checkpoint_path: Path,
        trace_path: Path | None = None,
        config: dict[str, object],
        product_ids_by_code: dict[str, str],
        uuid_factory: Callable[[], UUID | str] = uuid4,
    ) -> None:
        super().__init__(uuid_factory=uuid_factory)
        self.mode = "local"
        self._store_path = Path(store_path)
        self._checkpoint_path = Path(checkpoint_path)
        self._trace_path = (
            Path(trace_path)
            if trace_path is not None
            else self._checkpoint_path.parent / "p7_workbench_traces.jsonl"
        )
        self._config = config
        self._product_ids_by_code = product_ids_by_code
        self._evidence_index: dict[str, tuple[tuple[str, str, str], ...]] = {}

    def _product_view(self, code: str) -> ProductView:
        return _LOCAL_PRODUCTS[code]

    def _build_result(
        self, token: str, request: WorkbenchRunRequest, product_codes: tuple[str, ...]
    ) -> WorkbenchRun:
        try:
            product_ids = tuple(self._product_ids_by_code[code] for code in product_codes)
            products = tuple(self._product_view(code) for code in product_codes)
        except KeyError as error:
            raise WorkbenchInputError("Unknown controlled local product.") from error
        dependencies = AgentGraphDependencies(
            resolver=ProductResolver(self._store_path),
            gateway_factory=lambda budget: self._gateway(budget, request),
            tool_factory=lambda ledger: EvidenceToolService(
                self._store_path,
                expected_snapshot_id=str(self._config["dataset_snapshot_id"]),
                expected_contract_sha256=str(self._config["contract_sha256"]),
                retrieval_limit=int(self._config["retrieval_limit_per_aspect"]),
                ledger=ledger,
            ),
            trace_writer=lambda local, public: (
                append_local_trace(local, self._trace_path),
                serialize_public_trace(public),
            ),
            citation_verifier=lambda evidence_id: verify_citation(
                self._store_path, evidence_id
            ),
            snapshot_hash=str(self._config["dataset_snapshot_id"]),
            contract_hash=str(self._config["contract_sha256"]),
            model_id=str(self._config["model"]),
            config_id=str(self._config["config_sha256"]),
        )
        agent_request = AgentRequest(
            question=request.question,
            exact_product_ids=product_ids,
            aspects=(request.aspect,),
            workflow_hint=request.workflow,
        )
        with open_sqlite_agent_graph(
            dependencies,
            project_root=self._checkpoint_path.parent,
            checkpoint_path=self._checkpoint_path,
        ) as graph:
            state, graph_config = dependencies.new_run(agent_request, thread_id=token)
            state = graph.invoke(state, graph_config)
        return self._public_run(token, request, products, product_codes, state)

    @staticmethod
    def _gateway(
        budget: RequestBudget, request: WorkbenchRunRequest
    ) -> _DeterministicWorkbenchGateway:
        del budget
        return _DeterministicWorkbenchGateway(request.workflow, request.aspect.value)

    def _public_run(
        self,
        token: str,
        request: WorkbenchRunRequest,
        products: tuple[ProductView, ...],
        product_codes: tuple[str, ...],
        state: dict[str, object],
    ) -> WorkbenchRun:
        cards = tuple(
            SafeEvidenceCard.model_validate(item)
            for item in state.get("safe_evidence_cards", [])
        )
        local_by_alias = state.get("local_product_map", {})
        code_by_local = {
            self._product_ids_by_code[code]: code for code in product_codes
        }
        visible_cards = [card for card in cards if card.returned_evidence_count > 0]
        self._evidence_index[token] = tuple(
            (f"EV-{index:02d}", str(local_by_alias[card.product_alias]), str(card.aspect.value))
            for index, card in enumerate(visible_cards, start=1)
        )
        evidence = tuple(
            EvidenceView(
                evidence_alias=f"EV-{index:02d}",
                product_code=code_by_local[local_by_alias[card.product_alias]],
                aspect=card.aspect,
                polarity=self._card_polarity(card),
                strength=card.evidence_strength,
                excerpt=(
                    f"受控本地{_ASPECT_WORDING[request.aspect.value][0]}证据"
                    "已通过校验;评论原文保持私密。"
                ),
            )
            for index, card in enumerate(visible_cards, start=1)
        )
        comparison_rows = ()
        if request.workflow is Workflow.COMPARISON:
            comparison_rows = tuple(
                ComparisonRow(
                    product_code=code_by_local[local_by_alias[card.product_alias]],
                    signal=f"{_ASPECT_WORDING[request.aspect.value][0]}证据",
                    direction=(
                        "insufficient"
                        if card.returned_evidence_count == 0
                        else "watch"
                        if card.opposing_count or card.conflicting_count
                        else "advantage"
                    ),
                    strength=card.evidence_strength,
                    evidence_count=card.returned_evidence_count,
                    comparable=card.returned_evidence_count > 0,
                    insufficient_reason=(
                        None
                        if card.returned_evidence_count > 0
                        else "未返回受控本地证据。"
                    ),
                )
                for card in cards
            )
        triage_items = ()
        if request.workflow is Workflow.TRIAGE:
            triage_items = tuple(
                TriageItem(
                    rank=index,
                    issue=_ASPECT_WORDING[request.aspect.value][1],
                    priority="high" if card.evidence_strength == "STRONG" else "medium",
                    owner="Quality",
                    evidence_count=card.returned_evidence_count,
                )
                for index, card in enumerate(visible_cards, start=1)
            )
        graph_status = state.get("status")
        status = (
            graph_status
            if graph_status in {"VERIFIED_RESULT", "DEGRADED_SAFE_RESULT"}
            else "PROVIDER_ERROR"
        )
        evidence_count = sum(card.returned_evidence_count for card in cards)
        totals = {
            "supporting": sum(card.supporting_count for card in cards),
            "opposing": sum(card.opposing_count for card in cards),
            "conflicting": sum(card.conflicting_count for card in cards),
        }
        totals["insufficient"] = max(
            0, evidence_count - totals["supporting"] - totals["opposing"] - totals["conflicting"]
        )
        aspect_label = _ASPECT_WORDING[request.aspect.value][0]
        aspect_guidance = _ASPECT_WORDING[request.aspect.value][1]
        total_matched = self._total_matched(list(code_by_local), request.aspect.value)
        return WorkbenchRun(
            run_token=token,
            run_alias=_run_alias(token),
            status=status,
            workflow=request.workflow,
            title={
                Workflow.DIAGNOSIS: "本地 SKU 证据诊断",
                Workflow.COMPARISON: "本地竞品证据比较",
                Workflow.TRIAGE: "本地运营问题分诊",
            }[request.workflow],
            summary=(
                f"在这批冻结评论中,所选商品的“{aspect_label}”维度共匹配 {total_matched} 条;"
                f"本次按数据顺序取回 {evidence_count} 条。"
                f"机器按词表判定方向:支持 {totals['supporting']} 条、反对 {totals['opposing']} 条、"
                f"冲突 {totals['conflicting']} 条、信息不足 {totals['insufficient']} 条。"
            ),
            evidence_count=evidence_count,
            signal_totals=totals,
            degraded=status != "VERIFIED_RESULT",
            next_action=(
                f"重点:{aspect_guidance}。点击证据卡的「查看原文」逐条核对,"
                "被标“支持”的低分评论尤其需要人工确认。"
            ),
            products=products,
            evidence=evidence,
            comparison_rows=comparison_rows,
            triage_items=triage_items,
        )

    @staticmethod
    def _card_polarity(card: SafeEvidenceCard) -> str:
        if card.opposing_count:
            return "opposing"
        if card.conflicting_count:
            return "conflicting"
        return "supporting"

    def get_evidence_rows(self, run_token: str, evidence_alias: str) -> EvidenceDetail:
        """Return the on-demand review rows behind one evidence card (local only)."""

        if self.mode != "local" or self.get_run(run_token).status == "CLARIFICATION_REQUIRED":
            raise WorkbenchEvidenceUnavailable()
        entries = self._evidence_index.get(run_token, ())
        match = next((item for item in entries if item[0] == evidence_alias), None)
        if match is None:
            raise WorkbenchEvidenceUnavailable()
        _alias, product_id, aspect = match
        limit = int(self._config["retrieval_limit_per_aspect"])
        result = query_aspect(self._store_path, (product_id,), aspect, limit=limit)
        code = next(
            (code for code, pid in self._product_ids_by_code.items() if pid == product_id),
            product_id,
        )
        rows = []
        for evidence in result.evidence:
            if evidence.parent_asin != product_id or not verify_citation(
                self._store_path, evidence.evidence_id
            ):
                raise WorkbenchEvidenceUnavailable()
            rows.append(
                EvidenceRowView(
                    evidence_ref=evidence.evidence_id[:12],
                    locator_line=evidence.source_line,
                    rating=evidence.rating,
                    polarity=evidence.polarity,
                    helpful_votes=evidence.helpful_vote,
                    text=evidence.text.replace("\n", " ").strip()[:600],
                )
            )
        return EvidenceDetail(
            run_token=run_token,
            card_alias=evidence_alias,
            product_code=code,
            aspect=aspect,
            rows=tuple(rows),
        )

    def _total_matched(self, product_ids: list[str], aspect: str) -> int:
        """Count every frozen-contract match behind the bounded retrieval sample."""

        connection = sqlite3.connect(f"file:{self._store_path.as_posix()}?mode=ro", uri=True)
        try:
            vocabulary = connection.execute(
                "SELECT value FROM store_meta WHERE key = 'aspect_terms'"
            ).fetchone()
            if vocabulary is None or aspect not in json.loads(str(vocabulary[0])):
                raise WorkbenchInputError("Aspect is outside the local evidence contract.")
            terms = json.loads(str(vocabulary[0]))[aspect]
            predicates = " OR ".join("lower(text) LIKE ?" for _ in terms)
            placeholders = ",".join("?" for _ in product_ids)
            row = connection.execute(
                f"SELECT count(*) FROM reviews WHERE parent_asin IN ({placeholders}) "
                f"AND ({predicates})",
                [*product_ids, *[f"%{term.lower()}%" for term in terms]],
            ).fetchone()
        finally:
            connection.close()
        return int(row[0])
