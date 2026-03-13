from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re
import time
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MessageModel
from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.schemas.ai import AiRunRequest
from app.schemas.artifacts import (
    AlternativePreparation,
    ExploreCard,
    InterviewTurnOutput,
    Phase1ConversationalTurnOutput,
    Phase1InterviewTurnOutput,
    Phase1SlotSufficiencyOutput,
    Phase1StructuredOutput,
    Phase2CandidatesOutput,
    Phase2ExploreOutput,
    Phase3CommentsAndDraftsOutput,
    Phase3VotesOutput,
    Phase4PreparationOutput,
    Phase4RealityInterviewTurnOutput,
    Phase4RoadmapRow,
    Phase4RoadmapInterviewTurnOutput,
    PreparationItem,
    UnifiedCandidate,
)
from app.services.openai_service import OpenAIService
from app.services.repositories import (
    create_message,
    create_prompt_run,
    get_latest_artifact_by_type,
    get_session,
    upsert_artifact,
)
from app.services import file_store
from app.tasks.registry import TASK_PHASE_STEP_MAP


@dataclass
class TaskExecutionResult:
    output_json: dict
    prompt_run_id: UUID
    artifact_id: UUID | None


class TaskRunner:
    PHASE1_MAX_SLOT_ATTEMPTS = 2
    PHASE1_SUPPORT_MIN_CHARS = 10
    PHASE1_CORE_SLOTS: set[str] = {
        'values',
        'interests',
        'skills',
        'occupational_interests',
        'decision_style',
    }
    PHASE1_INITIAL_ASSISTANT_MESSAGE = (
        '안녕하세요. 지금부터 진로 의사결정에 필요한 정보를 항목별로 짧게 인터뷰해볼게요. '
        '먼저, 요즘 진로 고민을 시작하게 된 배경과 '
        '상황을 떠오르는 만큼 구체적으로 이야기해주실래요?'
    )
    PHASE1_COMPLETION_MESSAGE = (
        '좋아요. 1-1 인터뷰 정리가 완료됐어요. '
        '아래의 다음 단계 버튼을 눌러 요약 확인 단계로 이동해 주세요.'
    )
    PHASE4_REALITY_SLOTS: list[tuple[str, str]] = [
        ('work', '먼저, 현재 얼마나 일할 수 있는지(주당 시간/근무 가능 형태)를 알려주세요.'),
        ('experience', '다음으로, 직무 관련 봉사/실무 경험을 시도할 수 있는 여건이나 환경을 알려주세요.'),
        ('resource', '마지막으로, 준비를 위해 실제로 투입 가능한 시간/돈 범위를 알려주세요.'),
    ]

    PHASE1_TARGET_PLAN: list[tuple[str, str, str]] = [
        ('events', '사건', '의사결정을 요구하게 만든 구체적 사건'),
        ('significant_others', '주요 타인', '결정에 영향을 주는 중요한 사람들'),
        ('emotions', '정서', '선택 상황에서 느끼는 감정'),
        ('avoidance_behavior', '회피 행동', '결정을 피하거나 미루는 행동'),
        ('physical_feelings', '신체적 느낌', '스트레스/수면/피로 등 신체 반응'),
        ('values', '가치', '진로 선택에서 중요하게 보는 기준'),
        ('interests', '흥미', '몰입하거나 끌리는 활동'),
        ('skills', '기술', '상대적으로 강점이 있는 역량'),
        ('occupational_interests', '직업적 흥미', '일적으로 끌리는 분야/업무 맥락'),
        ('decision_style', '의사결정 방식', '과거부터 이어진 개인의 의사결정 스타일과 전략'),
        ('metacognition.self_talk', '자기 대화', '결정 순간 머릿속 문장'),
        ('metacognition.self_awareness', '자기 인식', '흔들릴 때 알아차리는 신호'),
        ('metacognition.control_and_monitoring', '조절 전략', '흔들릴 때 중심을 잡는 방법'),
    ]
    PHASE1_QUESTION_TEMPLATES: dict[str, str] = {
        'events': (
            '지금 결정을 내려야 한다고 느끼게 만든 외부 사건이나 상황을 구체적으로 말씀해주세요. '
            '예: 졸업, 취업 준비 압박, 이직, 주변 환경 변화'
        ),
        'significant_others': (
            '가족, 파트너, 친구, 선배처럼 중요한 사람들이 '
            '당신의 진로 의사결정에 어떤 영향을 주고 있는지 알려주세요.'
        ),
        'emotions': (
            '지금 진로 선택과 관련해 감정적으로 어떻게 느끼고 있는지 솔직하게 말씀해주세요. '
            '예: 불안, 답답함, 조급함, 기대'
        ),
        'avoidance_behavior': (
            '혹시 결정을 미루거나 피하기 위해 반복하는 행동이 있나요? '
            '예: 계속 비교만 하기, 실행 미루기, 생각만 반복하기'
        ),
        'physical_feelings': (
            '결정을 앞두고 몸에서는 어떤 반응이 나타나나요? '
            '예: 스트레스, 수면 문제, 피로, 식욕 변화, 생각 과다'
        ),
        'values': (
            '진로를 선택할 때 당신이 가장 중요하게 생각하는 가치를 알려주세요. '
            '예: 안정성, 성장, 자율성, 수입, 일의 의미'
        ),
        'interests': (
            '당신이 흥미를 느끼는 활동이나 주제를 구체적으로 알려주세요. '
            '시간 가는 줄 모르고 몰입했던 경험이 있으면 함께 말씀해주세요.'
        ),
        'skills': (
            '당신이 가지고 있다고 생각하는 기술이나 강점을 알려주세요. '
            '주변에서 인정받았던 역량이 있다면 함께 말씀해주세요.'
        ),
        'occupational_interests': (
            '직업적/업무적으로 관심이 가는 분야나 방식이 무엇인지 알려주세요. '
            '직업명이 아니어도 괜찮습니다.'
        ),
        'decision_style': (
            '과거에 중요한 결정을 내릴 때, 보통 어떤 방식과 전략을 사용했나요? '
            '당신의 의사결정 스타일을 설명해주세요.'
        ),
        'metacognition.self_talk': (
            '이 결정을 내리는 과정에서 스스로에게 어떤 말을 자주 건네나요? '
            '떠오르는 표현 그대로 알려주세요.'
        ),
        'metacognition.self_awareness': (
            '진로 고민이 커질 때, 스스로 “아 내가 흔들리고 있구나”를 '
            '어떤 신호로 알아차리는지 알려주세요.'
        ),
        'metacognition.control_and_monitoring': (
            '흔들리는 걸 알아차린 뒤, 실제로 방향을 다시 잡기 위해 '
            '어떤 행동을 하나요? (예: 기준 재정리, 마감 설정, 피드백 요청)'
        ),
    }
    PHASE1_CLARIFY_TEMPLATES: dict[str, str] = {
        'events': (
            '좋은 질문이에요. 거창한 사건이 아니어도 괜찮아요. '
            '최근 진로 고민이 커졌던 상황을 시간/장소/누구와 있었는지 중심으로 설명해주시면 됩니다.'
        ),
        'significant_others': (
            '여기서는 “누가” 그리고 “어떻게 영향 줬는지”를 알고 싶어요. '
            '예: 부모님이 안정적인 길을 권해 고민이 커졌다, 친구 취업 소식으로 마음이 급해졌다.'
        ),
        'emotions': (
            '감정은 정답이 없어요. 그 순간 내가 실제로 느낀 마음을 말해주시면 됩니다. '
            '예: 불안, 답답함, 조급함, 안도감.'
        ),
        'avoidance_behavior': (
            '회피 행동은 결정을 미루게 만드는 패턴을 뜻해요. '
            '예: 자료만 계속 찾고 결정을 미룸, 지원을 늦춤, 생각만 반복함.'
        ),
        'physical_feelings': (
            '신체 반응은 고민이 길어질 때 몸에서 나타나는 변화를 말해요. '
            '예: 잠이 줄어듦, 피로, 긴장, 소화 불편.'
        ),
        'values': (
            '가치는 진로에서 “무엇을 더 중요하게 둘지”에 대한 기준이에요. '
            '예: 안정성, 성장, 자율성, 수입, 워라밸, 사회적 기여.'
        ),
        'interests': (
            '흥미는 내가 에너지가 살아나는 활동/주제예요. '
            '예: 사람 문제 해결, 데이터 해석, 글쓰기, 기획, 만들기.'
        ),
        'skills': (
            '기술은 잘하거나 빠르게 익히는 역량이에요. '
            '예: 분석, 커뮤니케이션, 문서화, 협업, 실행력.'
        ),
        'occupational_interests': (
            '직업명이 아니어도 괜찮아요. 일에서 끌리는 방향을 말해주시면 됩니다. '
            '예: 사람과 협업 중심, 문제 해결 중심, 창작 중심, 운영 최적화 중심.'
        ),
        'decision_style': (
            '여기서는 결정을 내리는 “과정”을 묻는 거예요. '
            '예: 정보 수집 → 비교 → 우선순위 정리 → 실행처럼 평소 패턴을 알려주세요.'
        ),
        'metacognition.self_talk': (
            '자기 대화는 고민할 때 머릿속에서 반복되는 말이에요. '
            '예: “실수하면 안 돼”, “일단 해보자”.'
        ),
        'metacognition.self_awareness': (
            '자기 인식은 내가 흔들리는 신호를 알아차리는 방식이에요. '
            '예: 비교가 늘고 불안이 커질 때, 집중이 끊길 때.'
        ),
        'metacognition.control_and_monitoring': (
            '조절 전략은 흔들릴 때 다시 방향을 잡는 실제 행동이에요. '
            '예: 기준 3개를 다시 확인, 마감일 설정, 주변 피드백 요청.'
        ),
    }

    def __init__(self) -> None:
        self.settings = get_settings()
        self.openai_service = OpenAIService()

    async def run(self, db: AsyncSession, request: AiRunRequest, run_id: UUID) -> TaskExecutionResult:
        session = await get_session(db, request.session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='session not found')

        task_name = request.task_type
        if task_name not in TASK_PHASE_STEP_MAP:
            raise HTTPException(status_code=400, detail=f'unknown task_type: {task_name}')

        handler = getattr(self, f'_run_{task_name}')
        try:
            output_json, prompt_run_id, artifact_type = await handler(db, request, run_id)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            status_code, detail = self._map_unhandled_error(exc)
            await self._raise_task_error(
                db,
                request,
                run_id,
                status_code=status_code,
                detail=detail,
            )
            raise

        artifact_id = None
        if request.store_artifact and artifact_type:
            phase, step = TASK_PHASE_STEP_MAP[task_name]
            artifact = await upsert_artifact(
                db,
                session_id=request.session_id,
                phase=phase,
                step=step,
                artifact_type=artifact_type,
                payload=output_json,
                prompt_run_id=prompt_run_id,
            )
            artifact_id = artifact.id

        return TaskExecutionResult(
            output_json=output_json,
            prompt_run_id=prompt_run_id,
            artifact_id=artifact_id,
        )

    @staticmethod
    def _map_unhandled_error(exc: Exception) -> tuple[int, str]:
        raw = str(exc).strip()
        lowered = raw.lower()

        if 'insufficient_quota' in lowered:
            return (
                status.HTTP_503_SERVICE_UNAVAILABLE,
                'OpenAI 사용량 한도를 초과했습니다. OpenAI 결제/쿼터를 확인한 뒤 다시 시도해 주세요.',
            )
        if 'ratelimiterror' in lowered or 'rate limit' in lowered:
            return (
                status.HTTP_503_SERVICE_UNAVAILABLE,
                'OpenAI 요청 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.',
            )
        if 'authenticationerror' in lowered or 'invalid_api_key' in lowered:
            return (
                status.HTTP_503_SERVICE_UNAVAILABLE,
                'OpenAI API 키 인증에 실패했습니다. OPENAI_API_KEY 설정을 확인해 주세요.',
            )
        if 'tavily_api_key is required' in lowered:
            return (
                status.HTTP_503_SERVICE_UNAVAILABLE,
                '동적 검색에 필요한 Tavily API 키가 없습니다. TAVILY_API_KEY를 설정해 주세요.',
            )
        if 'connectionrefusederror' in lowered:
            return (
                status.HTTP_503_SERVICE_UNAVAILABLE,
                '외부 서비스 연결에 실패했습니다. 잠시 후 다시 시도해 주세요.',
            )
        if 'apitimeouterror' in lowered or 'request timed out' in lowered:
            return (
                status.HTTP_504_GATEWAY_TIMEOUT,
                '모델 응답 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.',
            )

        return (
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f'작업 처리 중 오류가 발생했습니다: {raw or type(exc).__name__}',
        )

    async def _run_phase1_interview_turn(self, db: AsyncSession, request: AiRunRequest, run_id: UUID):
        user_message = str(request.input_json.get('user_message', '')).strip()
        if user_message:
            await create_message(
                db,
                session_id=request.session_id,
                phase='phase1',
                step='1-1',
                role='user',
                content=user_message,
            )

        latest_messages = await self._get_latest_messages(db, request.session_id, phase='phase1', step='1-1')
        previous_assistant = self._phase1_latest_assistant_text(latest_messages)
        current_structured = await get_latest_artifact_by_type(db, request.session_id, 'phase1_structured')
        existing_structured = self._phase1_normalize_structured(
            current_structured.payload if current_structured else {}
        )
        state_artifact = await get_latest_artifact_by_type(db, request.session_id, 'phase1_interview_state')
        state_payload = state_artifact.payload if state_artifact else {}
        slot_keys = [key for key, _, _ in self.PHASE1_TARGET_PLAN]
        asked_slots = [
            str(v).strip() for v in list(state_payload.get('asked_slots', []) or []) if str(v).strip() in slot_keys
        ]
        slot_attempts_raw = state_payload.get('slot_attempts', {})
        slot_attempts: dict[str, int] = {}
        if isinstance(slot_attempts_raw, dict):
            for k, v in slot_attempts_raw.items():
                key = str(k).strip()
                if key in slot_keys:
                    try:
                        slot_attempts[key] = max(0, int(v))
                    except Exception:  # noqa: BLE001
                        slot_attempts[key] = 0
        clarify_counts_raw = state_payload.get('clarify_counts', {})
        clarify_counts = clarify_counts_raw if isinstance(clarify_counts_raw, dict) else {}

        state_current = str(state_payload.get('current_slot', '')).strip()
        current_slot = state_current if state_current in slot_keys else ''
        if not current_slot:
            for key in slot_keys:
                if key not in asked_slots:
                    current_slot = key
                    break
        is_initial_turn = len(latest_messages) == 0
        is_clarify_request = (
            bool(user_message)
            and bool(current_slot)
            and self._phase1_should_treat_as_clarification(user_message)
        )

        merged_snapshot = existing_structured
        prompt_run = None
        assistant_message = ''
        suggested_fields: list[str] = []
        state_current_slot = current_slot
        transition_ack = ''

        def next_unasked() -> str:
            for k in slot_keys:
                if k not in asked_slots:
                    return k
            return ''

        if is_initial_turn:
            output_stub = {
                'assistant_message': self.PHASE1_INITIAL_ASSISTANT_MESSAGE,
                'suggested_fields': ['events'],
                'structured_snapshot': existing_structured,
            }
            prompt_run = await create_prompt_run(
                db,
                session_id=request.session_id,
                run_id=run_id,
                task_type=request.task_type,
                prompt_version=request.prompt_version,
                model=request.model_override or self.settings.openai_model,
                input_json={
                    'initial_turn': True,
                    'current_slot': current_slot or 'events',
                },
                output_json=output_stub,
                latency_ms=0,
                error=None,
            )
            assistant_message = self.PHASE1_INITIAL_ASSISTANT_MESSAGE
            suggested_fields = ['events']
            state_current_slot = current_slot or 'events'
        elif not current_slot:
            prompt_run = await create_prompt_run(
                db,
                session_id=request.session_id,
                run_id=run_id,
                task_type=request.task_type,
                prompt_version=f'{request.prompt_version}:complete',
                model=request.model_override or self.settings.openai_model,
                input_json={'complete': True},
                output_json={'structured_snapshot': merged_snapshot},
                latency_ms=0,
                error=None,
            )
            assistant_message = self.PHASE1_COMPLETION_MESSAGE
            suggested_fields = []
            state_current_slot = ''
        elif user_message and is_clarify_request:
            clarify_counts[current_slot] = int(clarify_counts.get(current_slot, 0) or 0) + 1
            prompt_run = await create_prompt_run(
                db,
                session_id=request.session_id,
                run_id=run_id,
                task_type=request.task_type,
                prompt_version=f'{request.prompt_version}:clarify',
                model=request.model_override or self.settings.openai_model,
                input_json={
                    'current_slot': current_slot,
                    'user_message': user_message,
                    'clarify_count': clarify_counts[current_slot],
                },
                output_json={'structured_snapshot': merged_snapshot},
                latency_ms=0,
                error=None,
            )
            if int(clarify_counts.get(current_slot, 0) or 0) >= 2:
                if current_slot not in asked_slots:
                    asked_slots.append(current_slot)
                nxt = next_unasked()
                if nxt:
                    assistant_message = self.PHASE1_QUESTION_TEMPLATES.get(
                        nxt,
                        '다음 항목을 이야기해볼까요?',
                    )
                    suggested_fields = [nxt]
                    state_current_slot = nxt
                else:
                    assistant_message = self.PHASE1_COMPLETION_MESSAGE
                    suggested_fields = []
                    state_current_slot = ''
            else:
                assistant_message = self.PHASE1_CLARIFY_TEMPLATES.get(
                    current_slot,
                    self.PHASE1_QUESTION_TEMPLATES.get(current_slot, '해당 항목을 조금 더 설명해드릴게요.'),
                )
                suggested_fields = [current_slot]
                state_current_slot = current_slot
        elif user_message:
            extract_payload = {
                'current_slot': current_slot,
                'current_slot_focus': self._phase1_slot_focus(current_slot),
                'slot_guidance': self._phase1_slot_guidance(current_slot),
                'latest_user_message': user_message,
                'recent_user_messages': [
                    msg.get('content', '') for msg in latest_messages if msg.get('role') == 'user'
                ][-3:],
                'existing_structured': existing_structured,
                'instruction': (
                    'Extract only explicit information from user text for current_slot. '
                    'Do not infer missing facts.'
                ),
            }
            parsed, extract_prompt_run = await self.openai_service.run_structured(
                db=db,
                session_id=request.session_id,
                task_type=request.task_type,
                run_id=run_id,
                prompt_version=request.prompt_version,
                output_model=Phase1InterviewTurnOutput,
                system_prompt=(
                    'You are a structured extractor for career decision support.\n'
                    'Return valid JSON only.\n'
                    'All user-facing text values must be in Korean.\n'
                    'Do not expose CASVE original terms.\n'
                    'Update structured_snapshot with explicit facts from latest_user_message.\n'
                    'Use slot_guidance for the current slot.\n'
                    'If latest_user_message does not support a field, keep it unchanged from existing_structured.\n'
                    'Never hallucinate or generalize beyond user words.\n'
                    'assistant_message must be exactly one short Korean acknowledgment sentence only.\n'
                    'Do not ask follow-up questions in assistant_message.\n'
                    'assistant_message should paraphrase one concrete point from latest_user_message.\n'
                    'Do not copy long user phrases verbatim.\n'
                    'Avoid generic process words such as "정리", "구조화", "반영".\n'
                    'Do not use slot meta terms such as "사건 내용", "주요 타인 내용", "항목".'
                ),
                input_json=extract_payload,
                mock_output_factory=lambda: {
                    'assistant_message': '말씀해주신 내용 잘 이해했어요.',
                    'suggested_fields': [current_slot],
                    'structured_snapshot': existing_structured,
                },
                model_override=request.model_override,
            )
            prompt_run = extract_prompt_run
            transition_ack = str(parsed.assistant_message or '').strip()

            merged_candidate = self._merge_phase1_structured(
                existing_structured,
                parsed.structured_snapshot.model_dump(mode='json'),
            )
            merged_snapshot = self._phase1_keep_slot_update_only(
                existing_structured,
                merged_candidate,
                current_slot,
            )
            merged_snapshot = self._apply_phase1_heuristics(merged_snapshot, user_message, current_slot)
            merged_snapshot = self._sanitize_phase1_snapshot(merged_snapshot, existing_structured, user_message)
            if current_slot == 'significant_others':
                merged_snapshot = self._phase1_force_literal_significant_others(
                    merged_snapshot,
                    user_message,
                )
            merged_snapshot = self._phase1_clamp_core_lists(merged_snapshot)
            merged_snapshot = Phase1StructuredOutput.model_validate(merged_snapshot).model_dump(mode='json')

            slot_attempts[current_slot] = int(slot_attempts.get(current_slot, 0) or 0) + 1
            attempts = int(slot_attempts.get(current_slot, 0) or 0)
            rule_sufficient, rule_missing = self._phase1_rule_sufficiency_check(
                current_slot=current_slot,
                user_message=user_message,
                snapshot=merged_snapshot,
            )
            is_core_slot = current_slot in self.PHASE1_CORE_SLOTS
            is_sufficient = rule_sufficient
            missing_aspects = list(rule_missing)
            followup_question = ''

            if not is_core_slot:
                compact_len = len(re.sub(r'\s+', '', user_message))
                if compact_len >= self.PHASE1_SUPPORT_MIN_CHARS:
                    is_sufficient = True
                    missing_aspects = []
                else:
                    # For support slots, ask only one extra follow-up for very short inputs.
                    is_sufficient = attempts >= 2
                    if not is_sufficient:
                        missing_aspects = ['답변이 너무 짧아 한 번 더 구체화 필요']
            elif rule_sufficient:
                eval_payload = {
                    'current_slot': current_slot,
                    'slot_focus': self._phase1_slot_focus(current_slot),
                    'latest_user_message': user_message,
                    'structured_for_slot': self._phase1_get_slot_value(merged_snapshot, current_slot),
                    'slot_attempts': slot_attempts[current_slot],
                    'criteria': self._phase1_slot_success_criteria(current_slot),
                }
                eval_result, eval_prompt_run = await self.openai_service.run_structured(
                    db=db,
                    session_id=request.session_id,
                    task_type=request.task_type,
                    run_id=run_id,
                    prompt_version=f'{request.prompt_version}:slot_eval',
                    output_model=Phase1SlotSufficiencyOutput,
                    system_prompt=(
                        'You evaluate whether a user answer is sufficiently concrete for one interview slot.\n'
                        'Return valid JSON only.\n'
                        'If insufficient, provide exactly one short Korean follow-up question.\n'
                        'If sufficient, followup_question can be a short confirmation in Korean.\n'
                        'Focus on specificity and actionable detail.'
                    ),
                    input_json=eval_payload,
                    mock_output_factory=lambda: {
                        'is_sufficient': True,
                        'missing_aspects': [],
                        'followup_question': '',
                        'confidence': 0.85,
                    },
                    model_override=request.model_override,
                )
                prompt_run = eval_prompt_run
                is_sufficient = bool(eval_result.is_sufficient)
                missing_aspects = [str(v).strip() for v in eval_result.missing_aspects if str(v).strip()]
                followup_question = str(eval_result.followup_question or '').strip()

            if is_sufficient:
                if current_slot not in asked_slots:
                    asked_slots.append(current_slot)
                nxt = next_unasked()
                if nxt:
                    fallback_message = self._phase1_build_transition_to_next_question(
                        current_slot=current_slot,
                        user_message=user_message,
                        next_slot=nxt,
                        include_topic=True,
                        llm_ack=transition_ack,
                    )
                    assistant_message, convo_prompt_run = await self._phase1_generate_conversation_turn_message(
                        db=db,
                        request=request,
                        run_id=run_id,
                        mode='transition',
                        current_slot=current_slot,
                        next_slot=nxt,
                        user_message=user_message,
                        previous_assistant=previous_assistant,
                        fallback_message=fallback_message,
                        llm_ack=transition_ack,
                        missing_aspects=[],
                    )
                    if convo_prompt_run is not None:
                        prompt_run = convo_prompt_run
                    suggested_fields = [nxt]
                    state_current_slot = nxt
                else:
                    assistant_message = self.PHASE1_COMPLETION_MESSAGE
                    suggested_fields = []
                    state_current_slot = ''
            else:
                max_attempts = self.PHASE1_MAX_SLOT_ATTEMPTS if is_core_slot else 2
                if attempts >= max_attempts:
                    if current_slot not in asked_slots:
                        asked_slots.append(current_slot)
                    nxt = next_unasked()
                    if nxt:
                        fallback_message = self._phase1_build_transition_to_next_question(
                            current_slot=current_slot,
                            user_message=user_message,
                            next_slot=nxt,
                            include_topic=True,
                            llm_ack=transition_ack,
                        )
                        assistant_message, convo_prompt_run = await self._phase1_generate_conversation_turn_message(
                            db=db,
                            request=request,
                            run_id=run_id,
                            mode='transition',
                            current_slot=current_slot,
                            next_slot=nxt,
                            user_message=user_message,
                            previous_assistant=previous_assistant,
                            fallback_message=fallback_message,
                            llm_ack=transition_ack,
                            missing_aspects=[],
                        )
                        if convo_prompt_run is not None:
                            prompt_run = convo_prompt_run
                        suggested_fields = [nxt]
                        state_current_slot = nxt
                    else:
                        assistant_message = self.PHASE1_COMPLETION_MESSAGE
                        suggested_fields = []
                        state_current_slot = ''
                else:
                    fallback_message = self._phase1_build_slot_followup_question(
                        slot_key=current_slot,
                        llm_followup=followup_question,
                        missing_aspects=missing_aspects,
                        allow_llm_followup=(current_slot not in self.PHASE1_CORE_SLOTS),
                    )
                    assistant_message, convo_prompt_run = await self._phase1_generate_conversation_turn_message(
                        db=db,
                        request=request,
                        run_id=run_id,
                        mode='followup',
                        current_slot=current_slot,
                        next_slot=current_slot,
                        user_message=user_message,
                        previous_assistant=previous_assistant,
                        fallback_message=fallback_message,
                        llm_ack=transition_ack,
                        missing_aspects=missing_aspects,
                    )
                    if convo_prompt_run is not None:
                        prompt_run = convo_prompt_run
                    suggested_fields = [current_slot]
                    state_current_slot = current_slot
        else:
            prompt_run = await create_prompt_run(
                db,
                session_id=request.session_id,
                run_id=run_id,
                task_type=request.task_type,
                prompt_version=f'{request.prompt_version}:noop',
                model=request.model_override or self.settings.openai_model,
                input_json={'noop': True, 'current_slot': current_slot},
                output_json={'structured_snapshot': merged_snapshot},
                latency_ms=0,
                error=None,
            )
            if current_slot:
                assistant_message = self.PHASE1_QUESTION_TEMPLATES.get(current_slot, '다음 항목을 이야기해볼까요?')
                suggested_fields = [current_slot]
                state_current_slot = current_slot
            else:
                assistant_message = self.PHASE1_COMPLETION_MESSAGE
                suggested_fields = []
                state_current_slot = ''

        if (
            current_slot
            and state_current_slot == current_slot
            and previous_assistant
            and self._phase1_is_repetitive_assistant_reply(assistant_message, previous_assistant)
        ):
            if current_slot not in asked_slots:
                asked_slots.append(current_slot)
            nxt = next_unasked()
            if nxt:
                assistant_message = self._phase1_build_transition_to_next_question(
                    current_slot=current_slot,
                    user_message=user_message,
                    next_slot=nxt,
                    include_topic=False,
                    llm_ack='',
                )
                suggested_fields = [nxt]
                state_current_slot = nxt
            else:
                assistant_message = self.PHASE1_COMPLETION_MESSAGE
                suggested_fields = []
                state_current_slot = ''

        await create_message(
            db,
            session_id=request.session_id,
            phase='phase1',
            step='1-1',
            role='assistant',
            content=assistant_message,
        )

        await upsert_artifact(
            db,
            session_id=request.session_id,
            phase='phase1',
            step='1-1',
            artifact_type='phase1_structured',
            payload=merged_snapshot,
            prompt_run_id=prompt_run.id,
        )
        await upsert_artifact(
            db,
            session_id=request.session_id,
            phase='phase1',
            step='1-1',
            artifact_type='phase1_interview_state',
            payload={
                'current_slot': state_current_slot,
                'asked_slots': asked_slots,
                'slot_attempts': slot_attempts,
                'clarify_counts': clarify_counts,
                'remaining_slots': [key for key in slot_keys if key not in asked_slots],
            },
            prompt_run_id=prompt_run.id,
        )
        return {
            'assistant_message': assistant_message,
            'suggested_fields': suggested_fields,
            'structured_snapshot': merged_snapshot,
        }, prompt_run.id, None

    async def _run_phase1_extract_structured(self, db: AsyncSession, request: AiRunRequest, run_id: UUID):
        latest_messages = await self._get_latest_messages(db, request.session_id, phase='phase1', step='1-1')
        payload = {
            'messages': latest_messages,
            'goal': 'Extract a structured Korean summary for phase1 artifact update.',
        }

        parsed, prompt_run = await self.openai_service.run_structured(
            db=db,
            session_id=request.session_id,
            task_type=request.task_type,
            run_id=run_id,
            prompt_version=request.prompt_version,
            output_model=Phase1StructuredOutput,
            system_prompt=(
                'You are extracting structured outputs from a career interview.\n'
                'Return strict JSON only.\n'
                'All text values must be Korean.\n'
                'Keep each item concise and avoid CASVE original terms.'
            ),
            input_json=payload,
            mock_output_factory=lambda: {
                'events': ['대학 시절 디자인 프로젝트 참여가 진로 고민의 시작점이 됨'],
                'significant_others': ['멘토 교수', '선배 디자이너'],
                'emotions': ['기대감', '불안감'],
                'avoidance_behavior': ['결정을 미루며 자료 수집만 반복'],
                'physical_feelings': ['마감 직전 피로와 긴장 증가'],
                'values': ['창의성', '사용자 중심', '성장'],
                'interests': ['서비스 디자인', '사용자 리서치'],
                'skills': ['Figma', '프로토타이핑', '커뮤니케이션'],
                'occupational_interests': ['서비스 기획', 'UX 리서치'],
                'decision_style': '정보를 빠르게 모으지만 확신이 없으면 결정을 지연하는 경향',
                'metacognition': {
                    'self_talk': '실수하면 안 된다는 생각이 강해 결정을 늦춘다',
                    'self_awareness': '불안이 커지면 선택을 피하려는 경향이 있다',
                    'control_and_monitoring': '핵심 기준을 먼저 세우면 결정 속도가 빨라진다',
                },
            },
            model_override=request.model_override,
        )

        normalized = self._phase1_normalize_structured(parsed.model_dump(mode='json'))
        normalized['significant_others'] = self._phase1_compact_significant_others(
            normalized.get('significant_others', []),
            latest_user_message='',
        )
        normalized = self._phase1_clamp_core_lists(normalized)
        normalized = Phase1StructuredOutput.model_validate(normalized).model_dump(mode='json')
        return normalized, prompt_run.id, 'phase1_structured'

    async def _run_phase2_explore(self, db: AsyncSession, request: AiRunRequest, run_id: UUID):
        goal_query = str(
            request.input_json.get('goal_query', '적합한 진로 대안(직업/창업/진학/프로젝트 경로)을 탐색해줘')
        ).strip()
        phase1_summary_artifact = await get_latest_artifact_by_type(
            db, request.session_id, 'phase1_structured_confirmed'
        )
        if not phase1_summary_artifact:
            phase1_summary_artifact = await get_latest_artifact_by_type(
                db, request.session_id, 'phase1_structured'
            )
        summary_context = phase1_summary_artifact.payload if phase1_summary_artifact else {}
        phase1_messages = await self._get_latest_messages(db, request.session_id, phase='phase1', step='1-1', limit=80)
        user_utterances = [
            str(msg.get('content', '')).strip()
            for msg in phase1_messages
            if str(msg.get('role', '')).strip() == 'user' and str(msg.get('content', '')).strip()
        ]
        user_profile = self._phase2_infer_user_profile(summary_context, user_utterances)

        parsed, prompt_run = await self.openai_service.run_structured(
            db=db,
            session_id=request.session_id,
            task_type=request.task_type,
            run_id=run_id,
            prompt_version=request.prompt_version,
            output_model=Phase2ExploreOutput,
            system_prompt=(
                'Generate a single exploration board from the provided context.\n'
                'Return strict JSON only.\n'
                'All user-facing text values must be Korean.\n'
                'Each card must include id, title, tasks, work_environment, outlook.\n'
                'outlook should describe only outlook/trend in Korean (no salary numbers).\n'
                'Return exactly 6 cards.\n'
                'Important constraints:\n'
                '1) This task is LLM-only. Do not assume external retrieval data.\n'
                '2) Do not focus only on employment jobs. Include non-employment pathways as well '
                '(e.g., 창업, 대학원 진학, 프로젝트 트랙).\n'
                '3) Maximize alternative diversity. Avoid repeating the same title.\n'
                '4) Keep title and tasks semantically consistent. Do not mix unrelated duties.\n'
                '5) Avoid outdated labels. Prefer practical, modern pathway names.\n'
                '6) Reflect user_profile (especially major_track/domain clues). '
                'For graduate-school pathways, use natural domain-specific titles '
                '(e.g., 공학 대학원 진학, 인문사회 연구 트랙).'
            ),
            input_json={
                'goal_query': goal_query,
                'phase1_summary': summary_context,
                'user_profile': user_profile,
                'user_utterances': user_utterances[-20:],
            },
            mock_output_factory=lambda: {
                'cards': [
                    {
                        'id': 'alt-1',
                        'title': '공공 정책/제도 분석 트랙',
                        'tasks': '정책 자료를 분석하고 이슈 보고서·브리프를 작성함',
                        'work_environment': '공공기관/연구소 중심 협업 환경',
                        'outlook': '데이터 기반 정책 검토 수요가 꾸준히 유지되는 흐름',
                    },
                    {
                        'id': 'alt-2',
                        'title': '법·정책 대학원 진학 트랙',
                        'tasks': '관심 주제를 연구하고 논문/프로젝트 산출물을 축적함',
                        'work_environment': '대학원 연구실·세미나·프로젝트 중심',
                        'outlook': '전문성 심화와 장기 커리어 옵션 확장에 유리한 경향',
                    },
                    {
                        'id': 'alt-3',
                        'title': '공익 이슈 리서치·콘텐츠 트랙',
                        'tasks': '판례/정책 이슈를 구조화해 콘텐츠·리포트 형태로 발행함',
                        'work_environment': '개인 주도 프로젝트+외부 협업 병행',
                        'outlook': '전문 지식 기반 콘텐츠 수요와 개인 브랜딩 기회가 증가하는 흐름',
                    },
                    {
                        'id': 'alt-4',
                        'title': '법·정책 데이터 분석 트랙',
                        'tasks': '정책·판례 데이터를 정리하고 분석 리포트를 작성함',
                        'work_environment': '리서치·분석 중심 협업 환경',
                        'outlook': '데이터 기반 의사결정 지원 수요가 확장되는 흐름',
                    },
                    {
                        'id': 'alt-5',
                        'title': '공공기관 행정·법무 지원 트랙',
                        'tasks': '규정 검토와 문서 작성, 제도 운영 실무를 지원함',
                        'work_environment': '문서·협업 중심 기관 환경',
                        'outlook': '제도 운영 및 컴플라이언스 실무 수요가 안정적으로 유지되는 흐름',
                    },
                    {
                        'id': 'alt-6',
                        'title': '법·정책 기반 1인 프로젝트 트랙',
                        'tasks': '관심 이슈를 정해 리서치·브리프·콘텐츠 산출물을 축적함',
                        'work_environment': '개인 주도 + 외부 피드백 연계 환경',
                        'outlook': '포트폴리오 기반 경로를 확장하기 좋은 흐름',
                    },
                ]
            },
            model_override=request.model_override,
        )
        normalized_cards: list[dict[str, Any]] = []
        seen_titles: set[str] = set()
        for idx, raw in enumerate(list(parsed.model_dump(mode='json').get('cards') or []), start=1):
            if not isinstance(raw, dict):
                continue
            title = str(raw.get('title') or '').strip()
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            normalized_cards.append(
                ExploreCard(
                    id=str(raw.get('id') or f'alt-{idx}').strip() or f'alt-{idx}',
                    title=title,
                    tasks=str(raw.get('tasks') or '').strip(),
                    work_environment=str(raw.get('work_environment') or '').strip(),
                    outlook=str(raw.get('outlook') or '').strip(),
                ).model_dump(mode='json')
            )

        if len(normalized_cards) > 6:
            normalized_cards = normalized_cards[:6]

        fallback_cards = [
            ExploreCard(
                id='alt-f1',
                title='직무 중심 취업 트랙',
                tasks='핵심 직무를 정하고 관련 역량·포트폴리오를 준비함',
                work_environment='기업/기관 실무 협업 환경',
                outlook='직무 적합성 중심 채용 흐름이 유지되는 경향',
            ).model_dump(mode='json'),
            ExploreCard(
                id='alt-f2',
                title='도메인 특화 대학원 진학 트랙',
                tasks='관심 분야 연구 주제를 정해 학업·연구 기반 전문성을 심화함',
                work_environment='연구실/세미나 중심 학습 환경',
                outlook='중장기 전문성 확장과 진로 옵션 확대에 유리한 흐름',
            ).model_dump(mode='json'),
            ExploreCard(
                id='alt-f3',
                title='공공·정책 리서치 트랙',
                tasks='정책·제도 이슈를 조사하고 리포트/브리프를 작성함',
                work_environment='리서치 기관·프로젝트 협업 환경',
                outlook='근거 기반 정책 검토 수요가 이어지는 흐름',
            ).model_dump(mode='json'),
            ExploreCard(
                id='alt-f4',
                title='규정·컴플라이언스 실무 트랙',
                tasks='법규/규정 검토와 리스크 점검, 내부 프로세스 개선을 수행함',
                work_environment='문서·협업 중심 운영 환경',
                outlook='제도 준수 강화에 따라 실무 수요가 지속되는 흐름',
            ).model_dump(mode='json'),
            ExploreCard(
                id='alt-f5',
                title='프로젝트 기반 포트폴리오 트랙',
                tasks='작은 실전 프로젝트를 반복해 산출물을 포트폴리오로 축적함',
                work_environment='자기 주도 + 멘토 피드백 환경',
                outlook='성과물 중심 역량 검증 기회가 늘어나는 흐름',
            ).model_dump(mode='json'),
            ExploreCard(
                id='alt-f6',
                title='창업/사이드벤처 검증 트랙',
                tasks='문제 가설을 세우고 소규모 실험으로 수요를 검증함',
                work_environment='자율 실행 + 외부 피드백 환경',
                outlook='소규모 검증 기반 창업 진입 경로가 다양해지는 흐름',
            ).model_dump(mode='json'),
        ]

        for seed in fallback_cards:
            if len(normalized_cards) >= 6:
                break
            seed_title = str(seed.get('title') or '').strip()
            if not seed_title or seed_title in seen_titles:
                continue
            seen_titles.add(seed_title)
            normalized_cards.append(seed)

        while len(normalized_cards) < 6:
            idx = len(normalized_cards) + 1
            normalized_cards.append(
                ExploreCard(
                    id=f'alt-{idx}',
                    title=f'대안 {idx}',
                    tasks='사용자 입력 기반으로 실행 가능한 경로를 단계적으로 검토함',
                    work_environment='자료 조사 및 실행 점검 환경',
                    outlook='정보 보강에 따라 적합도 판단이 선명해지는 흐름',
                ).model_dump(mode='json')
            )

        normalized_cards = [{**card, 'id': f'alt-{i + 1}'} for i, card in enumerate(normalized_cards[:6])]

        output_json = Phase2ExploreOutput.model_validate({'cards': normalized_cards}).model_dump(mode='json')
        return output_json, prompt_run.id, 'phase2_explore_cards'

    async def _run_phase2_explore_chat_turn(self, db: AsyncSession, request: AiRunRequest, run_id: UUID):
        user_message = str(request.input_json.get('user_message', '')).strip()
        selected_card = request.input_json.get('selected_card') if isinstance(request.input_json.get('selected_card'), dict) else {}
        if user_message:
            await create_message(
                db,
                session_id=request.session_id,
                phase='phase2',
                step='2-1',
                role='user',
                content=user_message,
            )

        explore_artifact = await get_latest_artifact_by_type(db, request.session_id, 'phase2_explore_cards')
        explore_context = explore_artifact.payload if explore_artifact else {}
        latest_messages = await self._get_latest_messages(db, request.session_id, phase='phase2', step='2-1', limit=24)
        is_initial_turn = len(latest_messages) == 0 and not user_message

        if is_initial_turn:
            assistant_message = (
                '원하는 대안을 더 구체적으로 탐색해볼게요. '
                '궁금한 대안을 고른 뒤, 하는 일·전망·필요 역량처럼 알고 싶은 내용을 편하게 물어보세요.'
            )
            prompt_run = await create_prompt_run(
                db,
                session_id=request.session_id,
                run_id=run_id,
                task_type=request.task_type,
                prompt_version=f'{request.prompt_version}:initial',
                model=request.model_override or self.settings.openai_model,
                input_json={'initial_turn': True},
                output_json={'assistant_message': assistant_message, 'suggested_fields': []},
                latency_ms=0,
                error=None,
            )
            await create_message(
                db,
                session_id=request.session_id,
                phase='phase2',
                step='2-1',
                role='assistant',
                content=assistant_message,
            )
            return {
                'assistant_message': assistant_message,
                'suggested_fields': [],
            }, prompt_run.id, None

        parsed, prompt_run = await self.openai_service.run_structured(
            db=db,
            session_id=request.session_id,
            task_type=request.task_type,
            run_id=run_id,
            prompt_version=request.prompt_version,
            output_model=InterviewTurnOutput,
            system_prompt=(
                'You are a career-alternative Q&A assistant.\n'
                'Return strict JSON only.\n'
                'All user-facing text must be Korean.\n'
                'Use explore_cards and selected_card context when available.\n'
                'Answer the user question directly and sufficiently.\n'
                'Do not force "next action" suggestions unless user asks.\n'
                'If user asks for 전망, explain trend drivers and realistic outlook (no salary claims).\n'
                'If user asks for 사례, provide 1-2 concrete examples.\n'
                'If it is a follow-up question, add new detail instead of repeating prior phrasing.\n'
                'Avoid repeating generic lines from prior answers.\n'
                'Prefer 3-6 Korean sentences with concrete details.'
            ),
            input_json={
                'messages': latest_messages,
                'explore_cards': explore_context,
                'selected_card': selected_card,
            },
            mock_output_factory=lambda: {
                'assistant_message': '좋아요. 해당 대안의 하는 일, 현재 전망, 실제 업무 예시를 중심으로 구체적으로 설명해드릴게요.',
                'suggested_fields': [],
            },
            model_override=request.model_override,
        )

        await create_message(
            db,
            session_id=request.session_id,
            phase='phase2',
            step='2-1',
            role='assistant',
            content=parsed.assistant_message,
        )
        return parsed.model_dump(mode='json'), prompt_run.id, None

    async def _run_phase2_generate_candidates(self, db: AsyncSession, request: AiRunRequest, run_id: UUID):
        explore = request.input_json.get('explore')
        if not explore:
            artifact = await get_latest_artifact_by_type(db, request.session_id, 'phase2_explore_cards')
            if not artifact:
                await self._raise_task_error(
                    db,
                    request,
                    run_id,
                    status_code=status.HTTP_409_CONFLICT,
                    detail='phase2_generate_candidates requires phase2_explore_cards artifact',
                )
            explore = artifact.payload
        cards = list((explore if isinstance(explore, dict) else {}).get('cards') or [])
        target_count = 5
        min_count = 3

        parsed, prompt_run = await self.openai_service.run_structured(
            db=db,
            session_id=request.session_id,
            task_type=request.task_type,
            run_id=run_id,
            prompt_version=request.prompt_version,
            output_model=Phase2CandidatesOutput,
            system_prompt=(
                'Generate a unified candidate list from explore cards.\n'
                'Return strict JSON only.\n'
                'All user-facing text values must be Korean.\n'
                'Output unified_candidates only (single-agent structure).\n'
                'Create 3 to 5 diverse candidates grounded in input cards.\n'
                'Prefer 5 candidates unless source quality is insufficient.\n'
                'Each candidate requires id/title/summary/proposer.\n'
                'proposer should be a fixed string: \"AI 제안\".\n'
                'Do not use persona/agent labels or split output by persona.'
            ),
            input_json={'cards': cards},
            mock_output_factory=lambda: {
                'unified_candidates': [
                    {
                        'id': f'u{i + 1}',
                        'title': str(card.get('title') or f'대안 {i + 1}'),
                        'summary': str(card.get('tasks') or '').strip()[:120],
                        'proposer': 'AI 제안',
                    }
                    for i, card in enumerate(cards[:target_count])
                ],
            },
            model_override=request.model_override,
            timeout_sec=max(90.0, float(self.settings.openai_timeout_sec)),
        )

        rows: list[dict[str, Any]] = []
        seen_titles: set[str] = set()
        for idx, raw in enumerate(list(parsed.model_dump(mode='json').get('unified_candidates') or []), start=1):
            if not isinstance(raw, dict):
                continue
            title = str(raw.get('title') or '').strip()
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            rows.append(
                UnifiedCandidate(
                    id=str(raw.get('id') or f'u{idx}').strip() or f'u{idx}',
                    title=title,
                    summary=str(raw.get('summary') or '').strip(),
                    proposer='AI 제안',
                    similar=str(raw.get('similar') or '').strip() or None,
                ).model_dump(mode='json')
            )

        if len(rows) < target_count:
            for card in cards:
                title = str(card.get('title') or '').strip()
                if not title or title in seen_titles:
                    continue
                seen_titles.add(title)
                rows.append(
                    UnifiedCandidate(
                        id=f'u{len(rows) + 1}',
                        title=title,
                        summary=str(card.get('tasks') or '').strip()[:120],
                        proposer='AI 제안',
                    ).model_dump(mode='json')
                )
                if len(rows) >= target_count:
                    break

        while len(rows) < min_count:
            idx = len(rows) + 1
            rows.append(
                UnifiedCandidate(
                    id=f'u{idx}',
                    title=f'대안 {idx}',
                    summary='핵심 역할과 실행 경로를 비교해 적합도를 점검함',
                    proposer='AI 제안',
                ).model_dump(mode='json')
            )

        if len(rows) > target_count:
            rows = rows[:target_count]

        rows = [
            {**row, 'id': f'u{i + 1}'}
            for i, row in enumerate(rows)
        ]

        output_json = Phase2CandidatesOutput.model_validate({'unified_candidates': rows}).model_dump(mode='json')
        return output_json, prompt_run.id, 'phase2_candidates'

    async def _run_phase3_generate_comments_and_drafts(
        self, db: AsyncSession, request: AiRunRequest, run_id: UUID
    ):
        candidates = request.input_json.get('candidates')
        if not candidates:
            artifact = await get_latest_artifact_by_type(db, request.session_id, 'phase2_candidates')
            if not artifact:
                await self._raise_task_error(
                    db,
                    request,
                    run_id,
                    status_code=status.HTTP_409_CONFLICT,
                    detail='phase3_generate_comments_and_drafts requires phase2_candidates artifact',
                )
            candidates = artifact.payload
        candidate_payload = candidates if isinstance(candidates, dict) else {}
        unified = list(candidate_payload.get('unified_candidates') or [])
        if not unified:
            await self._raise_task_error(
                db,
                request,
                run_id,
                status_code=status.HTTP_409_CONFLICT,
                detail='phase3_generate_comments_and_drafts requires unified candidates in phase2_candidates artifact',
            )

        parsed, prompt_run = await self.openai_service.run_structured(
            db=db,
            session_id=request.session_id,
            task_type=request.task_type,
            run_id=run_id,
            prompt_version=request.prompt_version,
            output_model=Phase3CommentsAndDraftsOutput,
            system_prompt=(
                'Generate single-agent benefit/cost drafts for alternatives.\n'
                'Return strict JSON only.\n'
                'All user-facing text values must be Korean.\n'
                'For each alternative, generate exactly two cells: self, others.\n'
                'Each cell includes concise benefits/costs seed text.\n'
                'benefit_comments and cost_comments are optional short bullet-like hints (single-agent, no persona).\n'
                'Use concise declarative style such as "~함", "~수 있음", "~우려".\n'
                'Avoid polite endings "~입니다/에요/예요".'
            ),
            input_json={'unified_candidates': unified},
            mock_output_factory=lambda: {
                'alternatives': [
                    {
                        'alternative_id': str(item.get('id') or f'u{i + 1}'),
                        'alternative_title': str(item.get('title') or f'대안 {i + 1}'),
                        'cells': [
                            {
                                'perspective': 'self',
                                'benefits': '핵심 역량을 직접 활용하며 성장 체감을 얻을 수 있음',
                                'costs': '초기 적응 과정에서 시간·에너지 소모가 커질 수 있음',
                                'benefit_comments': ['성장 경로가 비교적 명확함'],
                                'cost_comments': ['초기 과부하 우려 있음'],
                            },
                            {
                                'perspective': 'others',
                                'benefits': '주요 타인과 기대 조율 포인트를 구체화하기 쉬움',
                                'costs': '주변 기대와 충돌할 경우 조정 비용이 발생할 수 있음',
                                'benefit_comments': ['설득 논리를 정리하기 쉬움'],
                                'cost_comments': ['합의 형성에 시간 필요'],
                            },
                        ],
                    }
                    for i, item in enumerate(unified)
                ]
            },
            model_override=request.model_override,
            timeout_sec=max(120.0, float(self.settings.openai_timeout_sec)),
        )
        validated = Phase3CommentsAndDraftsOutput.model_validate(parsed.model_dump(mode='json'))
        return validated.model_dump(mode='json'), prompt_run.id, 'phase3_comments_drafts'

    async def _run_phase3_generate_votes(self, db: AsyncSession, request: AiRunRequest, run_id: UUID):
        drafts = request.input_json.get('drafts')
        if not drafts:
            artifact = await get_latest_artifact_by_type(db, request.session_id, 'phase3_comments_drafts')
            if not artifact:
                await self._raise_task_error(
                    db,
                    request,
                    run_id,
                    status_code=status.HTTP_409_CONFLICT,
                    detail='phase3_generate_votes requires phase3_comments_drafts artifact',
                )
            drafts = artifact.payload

        parsed, prompt_run = await self.openai_service.run_structured(
            db=db,
            session_id=request.session_id,
            task_type=request.task_type,
            run_id=run_id,
            prompt_version=request.prompt_version,
            output_model=Phase3VotesOutput,
            system_prompt=(
                'Generate a single-agent recommendation ranking for alternatives.\n'
                'Return strict JSON only.\n'
                'All user-facing text values must be Korean.\n'
                'For each alternative provide recommendation_rank and recommendation_reason.\n'
                'Also provide recommended_alternative_id for the top recommendation.\n'
                'Do not use persona/agent fields.'
            ),
            input_json={'drafts': drafts},
            mock_output_factory=lambda: {
                'alternatives': [
                    {
                        'alternative_id': str(item.get('alternative_id') or f'u{i + 1}'),
                        'title': str(item.get('alternative_title') or f'대안 {i + 1}'),
                        'recommendation_rank': i + 1,
                        'recommendation_reason': '현재 입력 기준에서 실행 가능성과 적합도가 상대적으로 높음',
                    }
                    for i, item in enumerate(list((drafts if isinstance(drafts, dict) else {}).get('alternatives') or []))
                ],
                'recommended_alternative_id': str(
                    ((drafts if isinstance(drafts, dict) else {}).get('alternatives') or [{}])[0].get('alternative_id', '')
                ),
            },
            model_override=request.model_override,
        )
        output_json = parsed.model_dump(mode='json')
        alternatives = sorted(
            list(output_json.get('alternatives') or []),
            key=lambda item: int(item.get('recommendation_rank') or 999),
        )
        if alternatives and not output_json.get('recommended_alternative_id'):
            output_json['recommended_alternative_id'] = str(alternatives[0].get('alternative_id') or '')
        output_json['alternatives'] = alternatives
        return Phase3VotesOutput.model_validate(output_json).model_dump(mode='json'), prompt_run.id, 'phase3_votes'

    async def _run_phase4_generate_preparation(self, db: AsyncSession, request: AiRunRequest, run_id: UUID):
        votes = request.input_json.get('votes')
        if not votes:
            artifact = await get_latest_artifact_by_type(db, request.session_id, 'phase3_votes')
            if not artifact:
                await self._raise_task_error(
                    db,
                    request,
                    run_id,
                    status_code=status.HTTP_409_CONFLICT,
                    detail='phase4_generate_preparation requires phase3_votes artifact',
                )
            votes = artifact.payload

        compact_votes = self._phase4_compact_votes_for_preparation(
            votes if isinstance(votes, dict) else {}
        )
        if not list(compact_votes.get('alternatives') or []):
            await self._raise_task_error(
                db,
                request,
                run_id,
                status_code=status.HTTP_409_CONFLICT,
                detail='phase4_generate_preparation requires at least one selected alternative',
            )

        parsed, prompt_run = await self.openai_service.run_structured(
            db=db,
            session_id=request.session_id,
            task_type=request.task_type,
            run_id=run_id,
            prompt_version=request.prompt_version,
            output_model=Phase4PreparationOutput,
            system_prompt=(
                'Generate preparation plan items for selected alternatives.\n'
                'Return strict JSON only.\n'
                'All user-facing text must be Korean.\n'
                'This is SINGLE-ASSISTANT planning (not persona-specific).\n'
                'For each alternative, provide 3-6 concrete items.\n'
                'Each item must include category/title/detail and be executable by a career-prep user.\n'
                'Prefer specific channels/examples/resources over abstract wording.'
            ),
            input_json={'votes': compact_votes},
            mock_output_factory=lambda: self._mock_phase4_preparation(compact_votes),
            model_override=request.model_override,
            timeout_sec=max(90.0, float(self.settings.openai_timeout_sec)),
        )
        normalized = self._phase4_normalize_preparation_output(
            parsed.model_dump(mode='json'),
            votes_payload=compact_votes,
        )
        validated = Phase4PreparationOutput.model_validate(normalized)
        return validated.model_dump(mode='json'), prompt_run.id, 'phase4_preparation'

    async def _run_phase4_reality_interview_turn(self, db: AsyncSession, request: AiRunRequest, run_id: UUID):
        user_message = str(request.input_json.get('user_message', '')).strip()
        if user_message:
            await create_message(
                db,
                session_id=request.session_id,
                phase='phase4',
                step='4-2',
                role='user',
                content=user_message,
            )

        latest_messages = await self._get_latest_messages(db, request.session_id, phase='phase4', step='4-2')
        current_reality = await get_latest_artifact_by_type(db, request.session_id, 'phase4_reality_form')
        reality_snapshot = {
            'work': str((current_reality.payload if current_reality else {}).get('work', '')).strip(),
            'experience': str((current_reality.payload if current_reality else {}).get('experience', '')).strip(),
            'resource': str((current_reality.payload if current_reality else {}).get('resource', '')).strip(),
        }
        state_artifact = await get_latest_artifact_by_type(db, request.session_id, 'phase4_reality_state')
        state_payload = state_artifact.payload if state_artifact else {}
        slot_keys = [slot for slot, _ in self.PHASE4_REALITY_SLOTS]
        raw_inputs: dict[str, str] = {slot: '' for slot in slot_keys}
        raw_inputs_payload = state_payload.get('raw_inputs', {})
        if isinstance(raw_inputs_payload, dict):
            for slot in slot_keys:
                raw_inputs[slot] = str(raw_inputs_payload.get(slot, '')).strip()
        asked_slots = [
            str(v).strip()
            for v in list(state_payload.get('asked_slots', []) or [])
            if str(v).strip() in slot_keys
        ]
        current_slot = str(state_payload.get('current_slot', '')).strip()
        if current_slot not in slot_keys:
            current_slot = ''
        if not current_slot:
            for slot, _ in self.PHASE4_REALITY_SLOTS:
                if slot not in asked_slots:
                    current_slot = slot
                    break

        is_initial_turn = len(latest_messages) == 0 and not user_message
        suggested_fields: list[str] = []

        if is_initial_turn:
            current_slot = 'work'
            assistant_message = (
                '좋아요. 지금부터 실행 가능성을 확인하는 현실 조건 인터뷰를 진행할게요. '
                '시간/경험 여건/투입 자원을 순서대로 짧게 정리하겠습니다. '
                + dict(self.PHASE4_REALITY_SLOTS).get(current_slot, '')
            )
            suggested_fields = [current_slot]
        elif user_message:
            if not current_slot:
                current_slot = self._phase4_classify_slot_from_text(user_message)
            if self._phase1_should_treat_as_clarification(user_message):
                assistant_message = self._phase4_clarify_message(current_slot)
                suggested_fields = [current_slot]
            else:
                raw_inputs[current_slot] = self._phase4_append_raw_input(
                    raw_inputs.get(current_slot, ''),
                    user_message,
                )
                reality_snapshot[current_slot] = self._phase4_build_reality_slot_summary(
                    current_slot,
                    raw_inputs.get(current_slot, ''),
                )
                if current_slot not in asked_slots:
                    asked_slots.append(current_slot)
                next_slot = ''
                for slot, _ in self.PHASE4_REALITY_SLOTS:
                    if slot not in asked_slots:
                        next_slot = slot
                        break
                if next_slot:
                    current_slot = next_slot
                    assistant_message = dict(self.PHASE4_REALITY_SLOTS).get(
                        next_slot,
                        '다음 현실 조건을 알려주세요.',
                    )
                    suggested_fields = [next_slot]
                else:
                    current_slot = ''
                    assistant_message = (
                        '좋아요. 현실 조건 3가지를 모두 정리했어요. '
                        '중앙 입력칸 내용을 확인하고 수정한 뒤 다음 단계로 이동하세요.'
                    )
                    suggested_fields = []
        else:
            if current_slot:
                assistant_message = dict(self.PHASE4_REALITY_SLOTS).get(current_slot, '다음 현실 조건을 알려주세요.')
                suggested_fields = [current_slot]
            else:
                assistant_message = (
                    '현실 조건 정리가 완료되었습니다. 중앙 입력칸을 확인하고 다음 단계로 이동하세요.'
                )
                suggested_fields = []

        parsed = Phase4RealityInterviewTurnOutput.model_validate(
            {
                'assistant_message': assistant_message,
                'suggested_fields': suggested_fields,
                'reality_snapshot': reality_snapshot,
            }
        )
        prompt_run = await create_prompt_run(
            db,
            session_id=request.session_id,
            run_id=run_id,
            task_type=request.task_type,
            prompt_version=request.prompt_version,
            model=request.model_override or self.settings.openai_model,
            input_json={
                'current_slot': current_slot,
                'asked_slots': asked_slots,
                'user_message': user_message,
                'current_snapshot': reality_snapshot,
                'raw_slot_text': raw_inputs.get(current_slot, ''),
            },
            output_json=parsed.model_dump(mode='json'),
            latency_ms=0,
            error=None,
        )

        await create_message(
            db,
            session_id=request.session_id,
            phase='phase4',
            step='4-2',
            role='assistant',
            content=parsed.assistant_message,
        )

        await upsert_artifact(
            db,
            session_id=request.session_id,
            phase='phase4',
            step='4-2',
            artifact_type='phase4_reality_form',
            payload=parsed.reality_snapshot.model_dump(mode='json'),
            prompt_run_id=prompt_run.id,
        )
        await upsert_artifact(
            db,
            session_id=request.session_id,
            phase='phase4',
            step='4-2',
            artifact_type='phase4_reality_state',
            payload={
                'current_slot': current_slot,
                'asked_slots': asked_slots,
                'remaining_slots': [slot for slot, _ in self.PHASE4_REALITY_SLOTS if slot not in asked_slots],
                'raw_inputs': raw_inputs,
            },
            prompt_run_id=prompt_run.id,
        )

        return parsed.model_dump(mode='json'), prompt_run.id, None

    async def _run_phase4_roadmap_interview_turn(self, db: AsyncSession, request: AiRunRequest, run_id: UUID):
        user_message = str(request.input_json.get('user_message', '')).strip()
        if user_message:
            await create_message(
                db,
                session_id=request.session_id,
                phase='phase4',
                step='4-3',
                role='user',
                content=user_message,
            )

        latest_messages = await self._get_latest_messages(db, request.session_id, phase='phase4', step='4-3')
        prep_artifact = await get_latest_artifact_by_type(db, request.session_id, 'phase4_preparation')
        execution_plan_artifact = await get_latest_artifact_by_type(db, request.session_id, 'phase4_execution_plan')
        reality_artifact = await get_latest_artifact_by_type(db, request.session_id, 'phase4_reality_form')
        selected_artifact = await get_latest_artifact_by_type(db, request.session_id, 'phase3_final_selection')
        if not selected_artifact:
            selected_artifact = await get_latest_artifact_by_type(db, request.session_id, 'phase3_selected')
        roadmap_support = await get_latest_artifact_by_type(db, request.session_id, 'phase4_roadmap_support')
        roadmap_rows_artifact = await get_latest_artifact_by_type(db, request.session_id, 'phase4_roadmap_rows')
        current_rows = []
        if roadmap_rows_artifact and isinstance(roadmap_rows_artifact.payload, dict):
            rows_raw = roadmap_rows_artifact.payload.get('rows')
            if isinstance(rows_raw, list):
                current_rows = self._phase4_rows_for_model_prompt(rows_raw)

        parsed, prompt_run = await self.openai_service.run_structured(
            db=db,
            session_id=request.session_id,
            task_type=request.task_type,
            run_id=run_id,
            prompt_version=request.prompt_version,
            output_model=Phase4RoadmapInterviewTurnOutput,
            system_prompt=(
                'You are conducting a roadmap interview for execution planning.\n'
                'Return strict JSON only.\n'
                'All user-facing text values must be Korean.\n'
                'assistant_message should be one concise Korean turn with actionable guidance.\n'
                'roadmap_snapshot should update immediate_action, near_term_goal, key_risk_and_response.\n'
                'roadmap_rows must contain actionable rows with fields: action, deliverable, timing.\n'
                'Do not output internal labels such as r1/r2/roadmap-1 in any user-facing text.\n'
                'timing should use practical week/month windows (e.g., "1~2주", "3~4주", "1개월 내"), '
                'and should not include day-of-week granularity.\n'
                'Prioritize coherence with selected alternatives and phase4 execution plan when available.\n'
                'Each roadmap row should be traceable to selected alternative context or phase4-1 plan items.\n'
                'Avoid generic standalone advice. Include concrete object/scope in each action.\n'
                'Adapt flexibly to user intent, but keep the roadmap aligned with selected alternatives.\n'
                'If the user asks clarification, answer directly with concrete detail rather than generic restatement.\n'
                'Avoid vague placeholders like "주요 이해관계자" unless you provide concrete examples.\n'
                'Assume user is a preparation-stage individual. Prefer practical beginner-level actions '
                '(research, portfolio, networking, applications, short projects) over senior-level institutional tasks.'
            ),
            input_json={
                'messages': latest_messages,
                'selected_alternatives': selected_artifact.payload if selected_artifact else {},
                'phase4_1_summary': prep_artifact.payload if prep_artifact else {},
                'phase4_1_execution_plan': execution_plan_artifact.payload if execution_plan_artifact else {},
                'phase4_2_summary': reality_artifact.payload if reality_artifact else {},
                'current_snapshot': roadmap_support.payload if roadmap_support else {},
                'current_roadmap_rows': current_rows,
            },
            mock_output_factory=lambda: {
                'assistant_message': '이번 주에 바로 실행할 수 있는 가장 작은 액션 1개를 적어볼까요?',
                'suggested_fields': ['first_action'],
                'roadmap_snapshot': {
                    'immediate_action': '이번 주에 실행할 1개 액션을 구체화',
                    'near_term_goal': '3개월 내 달성할 상태를 명확화',
                    'key_risk_and_response': '장애요인 1개와 대응방법을 정리',
                },
                'roadmap_rows': [
                    {
                        'action': '이번 주 내 목표 대안 2개에 대한 정보 인터뷰 1회 진행',
                        'deliverable': '인터뷰 요약 1페이지',
                        'timing': '1주 이내',
                    },
                    {
                        'action': '2주 동안 대안별 소규모 실험 과제 1개씩 수행',
                        'deliverable': '실험 결과 비교 노트',
                        'timing': '2주',
                    },
                ],
            },
            model_override=request.model_override,
        )

        await create_message(
            db,
            session_id=request.session_id,
            phase='phase4',
            step='4-3',
            role='assistant',
            content=parsed.assistant_message,
        )

        await upsert_artifact(
            db,
            session_id=request.session_id,
            phase='phase4',
            step='4-3',
            artifact_type='phase4_roadmap_support',
            payload=parsed.roadmap_snapshot.model_dump(mode='json'),
            prompt_run_id=prompt_run.id,
        )
        refined_rows = self._phase4_refine_roadmap_rows(
            parsed.roadmap_rows,
            selected_payload=selected_artifact.payload if selected_artifact else {},
            execution_plan_payload=execution_plan_artifact.payload if execution_plan_artifact else {},
        )
        normalized_rows = []
        for idx, row in enumerate(refined_rows, start=1):
            normalized_rows.append(
                Phase4RoadmapRow(
                    id=f'roadmap-{idx}',
                    action=str(row.action).strip(),
                    deliverable=str(row.deliverable).strip(),
                    timing=str(row.timing).strip(),
                ).model_dump(mode='json')
            )
        await upsert_artifact(
            db,
            session_id=request.session_id,
            phase='phase4',
            step='4-3',
            artifact_type='phase4_roadmap_rows',
            payload={'rows': normalized_rows},
            prompt_run_id=prompt_run.id,
        )

        response_json = parsed.model_dump(mode='json')
        response_json['roadmap_rows'] = [row.model_dump(mode='json') for row in refined_rows]
        return response_json, prompt_run.id, None

    async def _run_phase4_2_interview_turn(self, db: AsyncSession, request: AiRunRequest, run_id: UUID):
        return await self._run_phase4_reality_interview_turn(db, request, run_id)

    async def _run_phase4_3_interview_turn(self, db: AsyncSession, request: AiRunRequest, run_id: UUID):
        return await self._run_phase4_roadmap_interview_turn(db, request, run_id)

    async def _raise_task_error(
        self,
        db: AsyncSession,
        request: AiRunRequest,
        run_id: UUID,
        *,
        status_code: int,
        detail: str,
    ) -> None:
        await create_prompt_run(
            db,
            session_id=request.session_id,
            run_id=run_id,
            task_type=request.task_type,
            prompt_version=request.prompt_version,
            model=request.model_override or self.settings.openai_model,
            input_json=request.input_json,
            output_json=None,
            latency_ms=None,
            error=detail,
        )
        raise HTTPException(status_code=status_code, detail={'message': detail, 'run_id': str(run_id)})

    async def _get_latest_messages(
        self, db: AsyncSession | None, session_id: UUID, *, phase: str, step: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        if db is None:
            return await file_store.get_messages_by_step(session_id, phase, step, limit=limit)
        q = (
            select(MessageModel)
            .where(
                MessageModel.session_id == session_id,
                MessageModel.phase == phase,
                MessageModel.step == step,
            )
            .order_by(MessageModel.created_at.desc())
            .limit(limit)
        )
        rows = list((await db.execute(q)).scalars().all())
        rows.reverse()
        return [{'role': row.role, 'content': row.content} for row in rows]

    @staticmethod
    def _phase1_empty_structured() -> dict[str, Any]:
        return {
            'events': [],
            'significant_others': [],
            'emotions': [],
            'avoidance_behavior': [],
            'physical_feelings': [],
            'values': [],
            'interests': [],
            'skills': [],
            'occupational_interests': [],
            'decision_style': '',
            'metacognition': {
                'self_talk': '',
                'self_awareness': '',
                'control_and_monitoring': '',
            },
        }

    @staticmethod
    def _phase1_normalize_structured(payload: dict[str, Any]) -> dict[str, Any]:
        base = TaskRunner._phase1_empty_structured()
        incoming = payload or {}
        for key in [
            'events',
            'significant_others',
            'emotions',
            'avoidance_behavior',
            'physical_feelings',
            'values',
            'interests',
            'skills',
            'occupational_interests',
        ]:
            raw = incoming.get(key)
            if isinstance(raw, list):
                base[key] = [str(v).strip() for v in raw if str(v).strip()]
        base['decision_style'] = str(incoming.get('decision_style', '')).strip()
        meta_in = incoming.get('metacognition') if isinstance(incoming.get('metacognition'), dict) else {}
        for sub in ['self_talk', 'self_awareness', 'control_and_monitoring']:
            base['metacognition'][sub] = str(meta_in.get(sub, '')).strip()
        return base

    @classmethod
    def _phase1_slot_focus(cls, slot_key: str) -> str:
        for key, _, focus in cls.PHASE1_TARGET_PLAN:
            if key == slot_key:
                return focus
        return ''

    @classmethod
    def _phase1_slot_label(cls, slot_key: str) -> str:
        for key, label, _ in cls.PHASE1_TARGET_PLAN:
            if key == slot_key:
                return label
        return ''

    @staticmethod
    def _phase1_extract_transition_topic(user_message: str, max_len: int = 28) -> str:
        text = ' '.join(str(user_message or '').split()).strip()
        if not text:
            return ''
        text = re.sub(r'[\"“”\'`]', '', text).strip()
        first_clause = re.split(r'[.!?\n]+', text)[0].strip()
        if len(first_clause) > max_len:
            first_clause = first_clause[:max_len].rstrip()
        first_clause = re.sub(r'[,:;]+$', '', first_clause).strip()
        if len(first_clause) < 4:
            return ''
        return first_clause

    @classmethod
    def _phase1_compact_question(cls, slot_key: str) -> str:
        raw = ' '.join(cls.PHASE1_QUESTION_TEMPLATES.get(slot_key, '다음 항목을 이야기해볼까요?').split()).strip()
        if not raw:
            return '다음 항목을 이야기해볼까요?'
        # Keep conversational question body only.
        raw = raw.split(' 예:')[0].strip()
        raw = raw.split(' 직업명이 아니어도 괜찮습니다.')[0].strip()
        return raw

    @staticmethod
    def _phase1_is_generic_ack(text: str) -> bool:
        v = ' '.join(str(text or '').split()).strip()
        if not v:
            return True
        generic_patterns = [
            '잘 정리',
            '구조화',
            '반영해',
            '반영해두',
            '공유해',
            '기록해',
            '내용을 잘',
            '잘 들었',
        ]
        return any(pattern in v for pattern in generic_patterns)

    @staticmethod
    def _phase1_ack_anchor_tokens(user_message: str) -> list[str]:
        text = ' '.join(str(user_message or '').split()).strip()
        if not text:
            return []
        tokens = re.findall(r'[가-힣A-Za-z0-9]+', text)
        stopwords = {
            '그리고', '그래서', '하지만', '그런데', '정도', '부분', '지금', '앞으로', '최근', '정말',
            '진로', '결정', '선택', '고민', '생각', '느낌', '느꼈다', '했다', '있다', '없다',
        }
        anchors: list[str] = []
        particles = ['으로', '에서', '에게', '께서', '까지', '부터', '처럼', '보다', '과', '와', '은', '는', '이', '가', '을', '를', '도', '만', '에', '로']

        def strip_particle(token: str) -> str:
            base = token
            for suffix in particles:
                if base.endswith(suffix) and len(base) > len(suffix) + 1:
                    base = base[: -len(suffix)]
                    break
            return base

        for token in tokens:
            if len(token) < 2:
                continue
            if token in stopwords:
                continue
            for candidate in [token, strip_particle(token)]:
                if len(candidate) < 2 or candidate in stopwords:
                    continue
                if candidate not in anchors:
                    anchors.append(candidate)
            if len(anchors) >= 6:
                break
        return anchors

    @classmethod
    def _phase1_ack_has_user_anchor(cls, ack: str, user_message: str) -> bool:
        anchors = cls._phase1_ack_anchor_tokens(user_message)
        if not anchors:
            return False
        normalized_ack = ' '.join(str(ack or '').split())
        return any(anchor in normalized_ack for anchor in anchors)

    @classmethod
    def _phase1_build_ack_fallback(cls, *, current_slot: str, user_message: str) -> str:
        topic = cls._phase1_extract_transition_topic(user_message, max_len=34)
        if topic and any(token in topic for token in ['고민', '불안', '중요', '원해', '선호', '힘들', '원하는', '느껴', '싶']):
            return f'{topic}라는 맥락이 분명하게 전해졌어요.'
        slot_label = cls._phase1_slot_label(current_slot)
        if slot_label:
            return f'{slot_label}에 대한 생각이 선명하게 느껴졌어요.'
        return '말씀하신 흐름이 잘 이해됐어요.'

    @staticmethod
    def _phase1_latest_assistant_text(messages: list[dict[str, Any]]) -> str:
        for msg in reversed(messages):
            if str(msg.get('role', '')).strip() != 'assistant':
                continue
            content = ' '.join(str(msg.get('content', '')).split()).strip()
            if content:
                return content
        return ''

    @staticmethod
    def _phase1_clean_single_sentence(text: str, *, max_len: int = 90) -> str:
        value = ' '.join(str(text or '').split()).strip()
        if not value:
            return ''
        value = re.sub(r'[\"“”\'`]', '', value).strip()
        value = value.split(' 예:')[0].strip()
        value = re.sub(r'\s+', ' ', value).strip()
        if len(value) > max_len:
            value = value[:max_len].rstrip()
        if value and value[-1] not in '.!?':
            value = f'{value}.'
        return value

    @staticmethod
    def _phase1_extract_question_from_fallback(fallback_message: str, next_slot: str) -> str:
        text = ' '.join(str(fallback_message or '').split()).strip()
        if not text:
            return ''
        if ' 이어서 ' in text:
            return text.split(' 이어서 ', 1)[1].strip()
        if next_slot:
            return TaskRunner._phase1_compact_question(next_slot)
        return text

    async def _phase1_generate_conversation_turn_message(
        self,
        *,
        db: AsyncSession,
        request: AiRunRequest,
        run_id: UUID,
        mode: str,
        current_slot: str,
        next_slot: str,
        user_message: str,
        previous_assistant: str,
        fallback_message: str,
        llm_ack: str,
        missing_aspects: list[str],
    ) -> tuple[str, Any | None]:
        fallback_text = ' '.join(str(fallback_message or '').split()).strip()
        if not fallback_text:
            return '', None

        target_question = self._phase1_extract_question_from_fallback(fallback_text, next_slot)
        ack_seed = self._phase1_clean_single_sentence(llm_ack, max_len=80)
        if (
            not ack_seed
            or self._phase1_is_generic_ack(ack_seed)
            or not self._phase1_ack_has_user_anchor(ack_seed, user_message)
        ):
            ack_seed = self._phase1_build_ack_fallback(current_slot=current_slot, user_message=user_message)

        payload = {
            'mode': mode,
            'current_slot': current_slot,
            'next_slot': next_slot,
            'latest_user_message': user_message,
            'previous_assistant_message': previous_assistant,
            'ack_seed': ack_seed,
            'target_next_question': target_question,
            'missing_aspects': [m for m in missing_aspects if str(m).strip()][:2],
        }
        try:
            parsed, prompt_run = await self.openai_service.run_structured(
                db=db,
                session_id=request.session_id,
                task_type=request.task_type,
                run_id=run_id,
                prompt_version=f'{request.prompt_version}:conversation',
                output_model=Phase1ConversationalTurnOutput,
                system_prompt=(
                    'You rewrite one Korean interview turn naturally.\n'
                    'Return strict JSON only.\n'
                    'ack_sentence: exactly one empathetic Korean sentence that paraphrases a concrete user point.\n'
                    'ack_sentence must not use process words like 정리/구조화/반영/항목/슬롯/체크.\n'
                    'Do not copy long fragments verbatim from latest_user_message.\n'
                    'next_question: exactly one Korean question sentence, aligned with target_next_question intent.\n'
                    'Keep it concise and natural (no long checklist style).\n'
                    'Do not include "예:" lists or parenthetical requirement lists.\n'
                    'Avoid repetitive opener pattern "좋아요. ... 이어서 ... 알려주세요".\n'
                    'If previous_assistant_message has similar wording, vary expression clearly.\n'
                ),
                input_json=payload,
                mock_output_factory=lambda: {
                    'ack_sentence': ack_seed,
                    'next_question': target_question,
                },
                model_override=request.model_override,
                timeout_sec=min(30.0, float(self.settings.openai_timeout_sec)),
            )
        except Exception:  # noqa: BLE001
            return fallback_text, None

        ack = self._phase1_clean_single_sentence(parsed.ack_sentence, max_len=90)
        question = ' '.join(str(parsed.next_question or '').split()).strip()
        question = question.split(' 예:')[0].strip()
        question = re.sub(r'\([^)]*(예시|기준|요건|형태)[^)]*\)', '', question).strip()
        if not question:
            question = target_question
        if question and question[-1] not in '?!':
            question = f'{question}?'
        if question.startswith('이어서 '):
            question = question[4:].strip()
        if not ack:
            ack = ack_seed

        candidate = f'{ack} {question}'.strip()
        if (
            not candidate
            or self._phase1_is_repetitive_assistant_reply(candidate, previous_assistant)
            or self._phase1_is_generic_ack(ack)
            or not self._phase1_ack_has_user_anchor(ack, user_message)
        ):
            return fallback_text, prompt_run

        return candidate, prompt_run

    @classmethod
    def _phase1_build_transition_to_next_question(
        cls,
        *,
        current_slot: str,
        user_message: str,
        next_slot: str,
        include_topic: bool,
        llm_ack: str = '',
    ) -> str:
        next_question = cls._phase1_compact_question(next_slot)

        ack = ' '.join(str(llm_ack or '').split()).strip()
        ack = re.sub(r'[\"“”\'`]', '', ack).strip()
        if ack:
            if ack.endswith('?'):
                ack = ''
            if any(
                token in ack
                for token in [
                    '사건 내용',
                    '주요 타인 내용',
                    '항목',
                    '슬롯',
                    '이어서',
                    '정리',
                    '구조화',
                    '반영',
                ]
            ):
                ack = ''
            if ack and len(ack) < 8:
                ack = ''
            if ack and ack[-1] not in '.!?':
                ack = f'{ack}.'
        if not ack or cls._phase1_is_generic_ack(ack) or not cls._phase1_ack_has_user_anchor(ack, user_message):
            ack = cls._phase1_build_ack_fallback(current_slot=current_slot, user_message=user_message)
        return f'{ack} {next_question}'

    @staticmethod
    def _phase1_slot_guidance(slot_key: str) -> str:
        if slot_key == 'significant_others':
            return 'Store statements in the form of "who influenced + how it affected decision".'
        if slot_key == 'events':
            return 'Capture one concrete trigger scene.'
        if slot_key == 'emotions':
            return 'Capture user own emotion words only.'
        if slot_key == 'avoidance_behavior':
            return 'Capture concrete avoidance pattern.'
        if slot_key == 'physical_feelings':
            return 'Capture physical symptoms linked to decision stress.'
        if slot_key == 'metacognition.self_awareness':
            return 'Capture awareness signals/triggers only (what signs user notices).'
        if slot_key == 'metacognition.control_and_monitoring':
            return 'Capture concrete regulation/monitoring actions after noticing signals.'
        return 'Capture concise explicit user text only.'

    @staticmethod
    def _phase1_slot_success_criteria(slot_key: str) -> str:
        criteria_map = {
            'events': 'Need concrete trigger context (situation + why it became decision pressure).',
            'significant_others': 'Need who influenced and how that influence affected decision.',
            'emotions': 'Need at least one clear emotion + brief context/reason.',
            'avoidance_behavior': 'Need concrete avoidant behavior pattern in action terms.',
            'physical_feelings': 'Need concrete physical symptoms linked to decision stress.',
            'values': 'Need concrete value(s) and why they matter.',
            'interests': 'Need concrete interest area/activity with example.',
            'skills': 'Need concrete strength/skill with brief evidence.',
            'occupational_interests': 'Need concrete work/role direction(s), not only vague label.',
            'decision_style': 'Need decision process pattern or strategy sequence.',
            'metacognition.self_talk': 'Need actual internal phrase or self-talk pattern.',
            'metacognition.self_awareness': 'Need signal of self-awareness and when it appears.',
            'metacognition.control_and_monitoring': 'Need concrete monitoring/control action used in practice.',
        }
        return criteria_map.get(slot_key, 'Need concrete and specific answer with context.')

    @staticmethod
    def _phase1_get_slot_value(snapshot: dict[str, Any], slot_key: str) -> Any:
        if slot_key.startswith('metacognition.'):
            subkey = slot_key.split('.', 1)[1]
            meta = snapshot.get('metacognition') if isinstance(snapshot.get('metacognition'), dict) else {}
            return str(meta.get(subkey, '')).strip()
        return snapshot.get(slot_key)

    @staticmethod
    def _phase1_rule_sufficiency_check(
        *,
        current_slot: str,
        user_message: str,
        snapshot: dict[str, Any],
    ) -> tuple[bool, list[str]]:
        text = ' '.join(str(user_message or '').split()).strip()
        if not text:
            return False, ['응답이 비어 있음']

        tokens = [t for t in re.split(r'\s+', text) if t]
        has_reason = any(k in text for k in ['때문', '라서', '그래서', '영향', '이유'])
        has_example_signal = any(k in text for k in ['예를', '예:', '예시', '최근', '구체', '실제'])
        has_separator = any(sep in text for sep in [',', '/', '·', '그리고', '및'])
        slot_value = TaskRunner._phase1_get_slot_value(snapshot, current_slot)
        if len(tokens) >= 12 and len(text) >= 30:
            return True, []

        if current_slot in {'values', 'interests', 'skills', 'occupational_interests'}:
            if len(tokens) <= 1 and not has_separator:
                return False, ['핵심 키워드가 너무 짧음', '구체적 설명 필요']
            if not has_reason and not has_example_signal and len(tokens) < 4:
                return False, ['왜 중요한지 또는 구체적 맥락 필요']
            return True, []

        if current_slot == 'events':
            if len(tokens) < 3:
                return False, ['사건 맥락이 짧음']
            if len(tokens) < 6 and not has_reason and not any(k in text for k in ['상황', '계기', '장면', '때']):
                return False, ['계기/상황 설명 필요']
            return True, []

        if current_slot == 'significant_others':
            person_clue = any(k in text for k in ['가족', '부모', '친구', '선배', '파트너', '교수', '주변', '동료'])
            if not person_clue:
                return False, ['영향을 준 사람(주체) 필요']
            if not has_reason and not any(k in text for k in ['영향', '기대', '권유', '압박', '조언']):
                return False, ['어떤 영향인지 설명 필요']
            return True, []

        if current_slot == 'emotions':
            emotion_clue = any(k in text for k in ['불안', '막막', '답답', '조급', '두렵', '걱정', '기대', '안도', '긴장'])
            if not emotion_clue and not slot_value:
                return False, ['감정 단어 필요']
            if len(tokens) <= 1:
                return False, ['감정이 느껴진 맥락 필요']
            return True, []

        if current_slot == 'avoidance_behavior':
            if not any(k in text for k in ['미루', '회피', '피하', '안 하', '계속 비교', '결정 못']):
                return False, ['회피/미루기 행동 묘사 필요']
            return True, []

        if current_slot == 'physical_feelings':
            if not any(k in text for k in ['잠', '수면', '피로', '식욕', '긴장', '두근', '소화', '몸', '스트레스']):
                return False, ['신체 반응 묘사 필요']
            return True, []

        if current_slot == 'decision_style':
            process_clue = any(k in text for k in ['먼저', '다음', '그다음', '비교', '정리', '우선순위', '판단'])
            if len(tokens) < 3 and not process_clue:
                return False, ['의사결정 절차/전략 설명 필요']
            return True, []

        if current_slot == 'metacognition.self_talk':
            compact_len = len(re.sub(r'\s+', '', text))
            if compact_len < 10:
                return False, ['자기 대화 문장을 조금만 더 구체적으로 작성해 주세요']
            return True, []

        if current_slot.startswith('metacognition.'):
            if len(tokens) < 2:
                return False, ['항목에 대한 구체적 문장 필요']
            return True, []

        return len(tokens) >= 4, (['구체성이 더 필요함'] if len(tokens) < 4 else [])

    @classmethod
    def _phase1_build_slot_followup_question(
        cls,
        *,
        slot_key: str,
        llm_followup: str,
        missing_aspects: list[str],
        allow_llm_followup: bool = True,
    ) -> str:
        candidate = ' '.join(str(llm_followup or '').split()).strip()
        if allow_llm_followup and candidate and len(candidate) >= 8:
            return candidate

        def sanitize_missing_aspect(raw: str) -> str:
            text = ' '.join(str(raw or '').split()).strip()
            if not text:
                return ''
            if '답변이 너무 짧아' in text:
                return ''
            text = re.sub(r'\s*없음$', '', text).strip()
            text = re.sub(r'\s*부족$', '', text).strip()
            text = text.replace('구체적 설명 필요', '구체적 설명')
            text = text.replace('구체화 필요', '구체화')
            text = text.replace('맥락 필요', '맥락')
            text = text.replace('필요', '').strip()
            text = re.sub(r'[,:;]+$', '', text).strip()
            return text

        normalized_missing: list[str] = []
        seen_missing: set[str] = set()
        for missing in missing_aspects:
            cleaned = sanitize_missing_aspect(missing)
            if not cleaned:
                continue
            if cleaned in seen_missing:
                continue
            seen_missing.add(cleaned)
            normalized_missing.append(cleaned)

        missing_text = ', '.join(normalized_missing[:2])[:120]
        suffix = f' 특히 {missing_text} 부분을 조금 더 구체적으로 알려주세요.' if missing_text else ''
        defaults = {
            'events': '좋아요. 그 사건이 실제로 어떤 상황에서 일어났는지 조금 더 구체적으로 알려주세요.',
            'significant_others': '좋아요. 누가 어떤 말/기대로 영향을 줬는지 한 번만 더 구체적으로 알려주세요.',
            'emotions': '좋아요. 그때 느꼈던 감정과, 왜 그런 감정이 들었는지 한 줄만 더 알려주세요.',
            'avoidance_behavior': '좋아요. 결정을 미루게 되는 행동을 실제 예시로 하나만 더 알려주세요.',
            'physical_feelings': '좋아요. 고민이 이어질 때 몸에서 나타나는 변화를 조금 더 구체적으로 알려주세요.',
            'values': '좋아요. 말씀하신 가치가 왜 중요한지, 당신 기준으로 한 번만 더 풀어주세요.',
            'interests': '좋아요. 흥미를 느낀 활동을 최근 경험 기준으로 하나만 더 구체적으로 알려주세요.',
            'skills': '좋아요. 그 강점이 드러났던 실제 사례를 짧게 알려주세요.',
            'occupational_interests': '좋아요. 일적으로 끌리는 방향을 업무 방식 기준으로 조금 더 구체화해 주세요.',
            'decision_style': '좋아요. 결정할 때 평소 순서(예: 수집→비교→결정)를 사례로 짧게 알려주세요.',
            'metacognition.self_talk': '좋아요. 그 상황에서 머릿속에 반복되는 문장을 있는 그대로 적어주세요.',
            'metacognition.self_awareness': '좋아요. 내가 흔들릴 때 알아차리는 신호를 실제 경험으로 짧게 알려주세요.',
            'metacognition.control_and_monitoring': '좋아요. 방향을 다시 잡기 위해 실제로 하는 행동을 한 가지 알려주세요.',
        }
        base = defaults.get(slot_key, '좋아요. 이 항목을 조금 더 구체적으로 알려주세요.')
        return base + suffix

    @staticmethod
    def _phase1_keep_slot_update_only(
        existing_snapshot: dict[str, Any],
        candidate_snapshot: dict[str, Any],
        slot_key: str,
    ) -> dict[str, Any]:
        if not slot_key:
            return candidate_snapshot

        result = TaskRunner._phase1_normalize_structured(existing_snapshot)
        candidate = TaskRunner._phase1_normalize_structured(candidate_snapshot)

        if slot_key.startswith('metacognition.'):
            subkey = slot_key.split('.', 1)[1]
            result['metacognition'][subkey] = candidate['metacognition'].get(subkey, '')
            return result

        result[slot_key] = candidate.get(slot_key)
        return result

    @staticmethod
    def _phase1_missing_targets(structured: dict[str, Any]) -> list[str]:
        missing: list[str] = []
        for key, _, _ in TaskRunner.PHASE1_TARGET_PLAN:
            if key.startswith('metacognition.'):
                _, subkey = key.split('.', 1)
                meta = structured.get('metacognition')
                if not isinstance(meta, dict) or not str(meta.get(subkey, '')).strip():
                    missing.append(key)
                continue
            value = structured.get(key)
            if isinstance(value, list):
                if len([v for v in value if str(v).strip()]) == 0:
                    missing.append(key)
            elif not str(value or '').strip():
                missing.append(key)
        return missing

    @staticmethod
    def _merge_phase1_structured(existing: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
        def merge_list(key: str) -> list[str]:
            out: list[str] = []
            seen: set[str] = set()
            for raw in list(existing.get(key, []) or []) + list(candidate.get(key, []) or []):
                item = str(raw).strip()
                if not item or item in seen:
                    continue
                seen.add(item)
                out.append(item)
            return out

        def pick_text(key: str) -> str:
            newer = str(candidate.get(key, '')).strip()
            older = str(existing.get(key, '')).strip()
            return newer or older

        existing_meta = existing.get('metacognition') if isinstance(existing.get('metacognition'), dict) else {}
        candidate_meta = candidate.get('metacognition') if isinstance(candidate.get('metacognition'), dict) else {}
        meta = {
            'self_talk': str(candidate_meta.get('self_talk', '')).strip()
            or str(existing_meta.get('self_talk', '')).strip(),
            'self_awareness': str(candidate_meta.get('self_awareness', '')).strip()
            or str(existing_meta.get('self_awareness', '')).strip(),
            'control_and_monitoring': str(candidate_meta.get('control_and_monitoring', '')).strip()
            or str(existing_meta.get('control_and_monitoring', '')).strip(),
        }

        merged = {
            'events': merge_list('events'),
            'significant_others': merge_list('significant_others'),
            'emotions': merge_list('emotions'),
            'avoidance_behavior': merge_list('avoidance_behavior'),
            'physical_feelings': merge_list('physical_feelings'),
            'values': merge_list('values'),
            'interests': merge_list('interests'),
            'skills': merge_list('skills'),
            'occupational_interests': merge_list('occupational_interests'),
            'decision_style': pick_text('decision_style'),
            'metacognition': meta,
        }
        return merged

    @staticmethod
    def _apply_phase1_heuristics(snapshot: dict[str, Any], user_message: str, current_slot: str) -> dict[str, Any]:
        text = user_message.strip()
        if not text:
            return snapshot

        def add_unique(list_key: str, value: str) -> None:
            items = list(snapshot.get(list_key, []) or [])
            if value not in items:
                items.append(value)
            snapshot[list_key] = items

        if current_slot == 'significant_others':
            add_unique('significant_others', text)

        if current_slot == 'emotions' and any(token in text for token in ['막막', '불안', '두렵', '답답', '초조', '스트레스']):
            for token, normalized in [
                ('막막', '막막함'),
                ('불안', '불안'),
                ('두렵', '두려움'),
                ('답답', '답답함'),
                ('초조', '초조함'),
                ('스트레스', '스트레스'),
            ]:
                if token in text:
                    add_unique('emotions', normalized)
        if current_slot == 'emotions' and '걱정' in text:
            if TaskRunner._is_first_person_worry(text):
                add_unique('emotions', '걱정')
        if current_slot == 'avoidance_behavior' and any(token in text for token in ['미루', '회피', '안 하', '포기']):
            add_unique('avoidance_behavior', '결정/행동을 미루는 경향')
        if current_slot == 'physical_feelings' and any(token in text for token in ['잠', '수면', '두근', '심장', '긴장', '피곤', '피로', '소화']):
            add_unique('physical_feelings', '수면/긴장 등 신체 반응')
        if current_slot == 'events' and any(token in text for token in ['하라고', '권유', '압박', '강요', '시작', '상황', '계기']):
            add_unique('events', text[:60])
        if current_slot.startswith('metacognition.'):
            meta = snapshot.get('metacognition') if isinstance(snapshot.get('metacognition'), dict) else {}
            subkey = current_slot.split('.', 1)[1]
            cleaned = text[:180]
            if cleaned:
                meta[subkey] = cleaned
                snapshot['metacognition'] = meta
        return snapshot

    @classmethod
    def _phase1_build_followup_question(cls, next_target: str, user_message: str) -> str:
        if not next_target:
            return '좋아요. 계속 진행해볼게요. 다음 단계에서 선택 기준을 더 구체화해보겠습니다.'

        base_question = cls.PHASE1_QUESTION_TEMPLATES.get(
            next_target,
            '좋아요. 이 부분을 조금만 더 구체적으로 말해주실래요?',
        )

        lowered = user_message.strip()
        if cls._phase1_is_clarification_request(lowered):
            return cls.PHASE1_CLARIFY_TEMPLATES.get(next_target, base_question)
        if any(token in lowered for token in ['아까 말', '이미 말', '위에서 말', '말했잖']):
            return (
                '알겠어요. 이미 말씀해주신 내용은 반영해둘게요. '
                + base_question
            )
        return base_question

    @classmethod
    def _phase1_finalize_assistant_message(
        cls,
        *,
        is_initial_turn: bool,
        llm_assistant_message: str,
        next_target: str,
        current_slot: str,
        user_message: str,
        latest_messages: list[dict[str, Any]],
    ) -> str:
        if is_initial_turn:
            return cls.PHASE1_INITIAL_ASSISTANT_MESSAGE

        fallback_target = next_target or current_slot
        fallback_message = cls._phase1_build_followup_question(fallback_target, user_message)

        candidate = ' '.join(str(llm_assistant_message or '').split()).strip()
        if not candidate:
            return fallback_message

        previous_assistant = ''
        for msg in reversed(latest_messages):
            if str(msg.get('role', '')).strip() == 'assistant':
                previous_assistant = ' '.join(str(msg.get('content', '')).split()).strip()
                if previous_assistant:
                    break

        if previous_assistant and cls._phase1_is_repetitive_assistant_reply(candidate, previous_assistant):
            if cls._phase1_is_clarification_request(user_message):
                return cls.PHASE1_CLARIFY_TEMPLATES.get(current_slot or fallback_target, fallback_message)
            return fallback_message

        if len(candidate) < 8:
            return fallback_message

        return candidate

    @staticmethod
    def _phase1_is_repetitive_assistant_reply(candidate: str, previous: str) -> bool:
        c = TaskRunner._phase1_fingerprint(candidate)
        p = TaskRunner._phase1_fingerprint(previous)
        if not c or not p:
            return False
        if c == p:
            return True
        if c in p or p in c:
            shorter = min(len(c), len(p))
            return shorter >= 18
        return False

    @staticmethod
    def _phase1_fingerprint(text: str) -> str:
        t = re.sub(r'\s+', '', str(text or '').strip())
        t = re.sub(r'[^\w가-힣]', '', t)
        return t.lower()

    @staticmethod
    def _phase1_is_clarification_request(text: str) -> bool:
        t = text.strip()
        if not t:
            return False
        clues = [
            '무슨',
            '어떤',
            '뭘',
            '무엇',
            '예시',
            '뜻',
            '말하는 거',
            '말하는거',
            '애매',
            '잘 모르',
            '모르겠',
        ]
        return ('?' in t) or any(token in t for token in clues)

    @classmethod
    def _phase1_should_treat_as_clarification(cls, text: str) -> bool:
        t = text.strip()
        if not cls._phase1_is_clarification_request(t):
            return False
        compact_len = len(re.sub(r'\s+', '', t))
        if compact_len >= cls.PHASE1_SUPPORT_MIN_CHARS:
            return False
        # If the user message already carries substantial self-description,
        # treat it as a valid answer even when it includes a question mark.
        tokens = [tok for tok in re.split(r'\s+', t) if tok]
        if len(tokens) >= 10:
            return False
        substantive_clues = [
            '나는',
            '제가',
            '나는 보통',
            '느꼈',
            '경향',
            '최근',
            '상황',
            '결정',
            '불안',
            '막막',
            '전략',
        ]
        if any(clue in t for clue in substantive_clues):
            return False
        return True

    @staticmethod
    def _phase1_choose_next_target(missing_targets: list[str], user_message: str) -> str:
        if not missing_targets:
            return ''
        first = missing_targets[0]
        lowered = user_message.strip()
        resistance = any(token in lowered for token in ['아까 말', '이미 말', '위에서 말', '말했잖'])
        if resistance and first == 'events':
            for key in missing_targets[1:]:
                if key != 'events':
                    return key
        return first

    @staticmethod
    def _phase4_classify_slot_from_text(text: str) -> str:
        t = str(text or '')
        if any(keyword in t for keyword in ['돈', '비용', '예산', '지출', '학원비', '투입']):
            return 'resource'
        if any(keyword in t for keyword in ['경험', '봉사', '인턴', '프로젝트', '실습', '현장']):
            return 'experience'
        return 'work'

    @staticmethod
    def _phase4_append_raw_input(existing: str, incoming: str) -> str:
        previous = ' '.join(str(existing or '').split()).strip()
        current = ' '.join(str(incoming or '').split()).strip()
        if not current:
            return previous
        if not previous:
            return current
        if current in previous:
            return previous
        return f'{previous} {current}'

    @staticmethod
    def _phase4_build_reality_slot_summary(slot_key: str, raw_text: str) -> str:
        text = ' '.join(str(raw_text or '').split()).strip()
        if not text:
            return ''

        def uniq(values: list[str]) -> list[str]:
            seen: set[str] = set()
            output: list[str] = []
            for value in values:
                item = str(value or '').strip()
                if not item or item in seen:
                    continue
                seen.add(item)
                output.append(item)
            return output

        if slot_key == 'work':
            status_tokens: list[str] = []
            for keyword, label in [
                ('재직', '재직 상태'),
                ('근무', '근무 가능'),
                ('직장', '직장 병행'),
                ('학생', '학업 병행'),
                ('재학', '학업 병행'),
                ('취준', '취업 준비 병행'),
                ('육아', '가정/돌봄 병행'),
                ('알바', '아르바이트 병행'),
            ]:
                if keyword in text:
                    status_tokens.append(label)

            time_tokens = re.findall(
                r'(주\s*\d+\s*시간|평일\s*[^,.\n]{0,14}\d+\s*시간|주말\s*[^,.\n]{0,14}\d+\s*시간|하루\s*\d+\s*시간)',
                text,
            )
            mode_tokens = [
                keyword
                for keyword in ['원격', '재택', '대면', '하이브리드', '주말 중심', '평일 저녁', '풀타임', '파트타임']
                if keyword in text
            ]
            if '평일' in text and '풀타임' in text and '평일 풀타임' not in mode_tokens:
                mode_tokens.append('평일 풀타임')

            lines: list[str] = []
            if status_tokens:
                lines.append(f"현재 상태: {', '.join(uniq(status_tokens))}")
            else:
                lines.append('현재 상태: 추가 확인 필요')

            if time_tokens or mode_tokens:
                merged = uniq(time_tokens + mode_tokens)
                lines.append(f"근무 가능 시간/형태: {', '.join(merged)}")
            else:
                lines.append(f'근무 가능 시간/형태: {text[:90]}')
            return '\n'.join(uniq(lines))

        if slot_key == 'experience':
            channel_tokens = [
                keyword
                for keyword in ['인턴', '프로젝트', '봉사', '현장실습', '스터디', '멘토링', '아르바이트', '연구실', '동아리']
                if keyword in text
            ]
            env_tokens = [
                keyword
                for keyword in ['학교', '커뮤니티', '지인', '주변 사람', '회사', '기관', '온라인', '오프라인']
                if keyword in text
            ]
            limit_tokens = [keyword for keyword in ['어렵', '부족', '제약', '없', '제한'] if keyword in text]
            lines = []
            lines.append(
                f"가능한 경험 경로: {', '.join(uniq(channel_tokens))}"
                if channel_tokens
                else f'가능한 경험 경로: {text[:80]}'
            )
            if env_tokens:
                lines.append(f"활용 가능한 환경/네트워크: {', '.join(uniq(env_tokens))}")
            if limit_tokens:
                lines.append('제약 사항: 일정·기회 제약 확인 필요')
            return '\n'.join(uniq(lines))

        if slot_key == 'resource':
            money_tokens = re.findall(r'(월\s*\d+\s*만원|\d+\s*만원|\d+\s*원)', text)
            time_tokens = re.findall(r'(주\s*\d+\s*시간|하루\s*\d+\s*시간)', text)
            support_tokens = [keyword for keyword in ['국비', '장학금', '지원금', '저축', '부모 지원', '대출'] if keyword in text]
            has_time_signal = bool(time_tokens) or any(keyword in text for keyword in ['시간', '여유', '가능'])
            has_money_signal = bool(money_tokens) or any(keyword in text for keyword in ['돈', '비용', '예산', '금전'])
            lines = []
            lines.append(
                f"시간 예산: {', '.join(uniq(time_tokens))}"
                if time_tokens
                else ('시간 예산: 확보 가능(세부 수치 미입력)' if has_time_signal else '시간 예산: 추가 확인 필요')
            )
            lines.append(
                f"금전 예산: {', '.join(uniq(money_tokens))}"
                if money_tokens
                else ('금전 예산: 투입 가능(세부 수치 미입력)' if has_money_signal else '금전 예산: 추가 확인 필요')
            )
            if support_tokens:
                lines.append(f"활용 가능 자원: {', '.join(uniq(support_tokens))}")
            return '\n'.join(uniq(lines))

        return text[:160]

    @staticmethod
    def _phase4_merge_reality_text(existing: str, incoming: str, slot_key: str) -> str:
        previous = ' '.join(str(existing or '').split()).strip()
        current = TaskRunner._phase4_structured_slot_value(slot_key, incoming)
        if not current:
            return previous
        if not previous:
            return current
        if current in previous:
            return previous
        if previous in current:
            return current
        return f'{previous}\n- {current}'

    @staticmethod
    def _phase4_structured_slot_value(slot_key: str, incoming: str) -> str:
        text = ' '.join(str(incoming or '').split()).strip()
        if not text:
            return ''

        if slot_key == 'work':
            status = ''
            if any(k in text for k in ['재직', '근무 중', '직장']):
                status = '현재 근무 병행'
            elif any(k in text for k in ['학생', '재학']):
                status = '학업 병행'
            elif any(k in text for k in ['휴학', '공백']):
                status = '공백/전환기'
            time_matches = re.findall(r'(주\\s*\\d+\\s*시간|평일\\s*\\d+\\s*시간|주말\\s*\\d+\\s*시간)', text)
            parts = [status] if status else []
            parts.extend(time_matches)
            if parts:
                return '근무 가능 조건: ' + ', '.join(dict.fromkeys(parts))
            return '근무 가능 조건: ' + text

        if slot_key == 'experience':
            channels = []
            for keyword in ['인턴', '프로젝트', '봉사', '스터디', '현장실습', '아르바이트', '멘토링']:
                if keyword in text:
                    channels.append(keyword)
            if channels:
                return '경험 가능 여건: ' + ', '.join(dict.fromkeys(channels)) + ' 중심으로 진행 가능'
            return '경험 가능 여건: ' + text

        if slot_key == 'resource':
            money = re.findall(r'(월\\s*\\d+\\s*만원|\\d+\\s*만원|\\d+\\s*원)', text)
            time_budget = re.findall(r'(주\\s*\\d+\\s*시간|하루\\s*\\d+\\s*시간)', text)
            parts = []
            if money:
                parts.append('예산 ' + ', '.join(dict.fromkeys(money)))
            if time_budget:
                parts.append('시간 ' + ', '.join(dict.fromkeys(time_budget)))
            if parts:
                return '투입 자원: ' + ' / '.join(parts)
            return '투입 자원: ' + text

        return text

    @staticmethod
    def _phase4_clarify_message(slot_key: str) -> str:
        templates = {
            'work': (
                '좋아요. 여기서는 실제로 쓸 수 있는 시간/근무 형태를 알고 싶어요. '
                '예: 평일 2시간, 주말 4시간처럼 알려주세요.'
            ),
            'experience': (
                '여기서는 직무 관련 경험을 시도할 수 있는 여건을 묻고 있어요. '
                '예: 프로젝트 참여 가능, 봉사 가능, 인턴 지원 가능 여부를 알려주세요.'
            ),
            'resource': (
                '여기서는 준비에 투입 가능한 시간/돈 범위를 확인해요. '
                '예: 월 20만원, 주당 10시간처럼 말해주시면 됩니다.'
            ),
        }
        return templates.get(slot_key, '좋아요. 이 항목을 조금 더 구체적으로 알려주세요.')

    @staticmethod
    def _phase4_compact_votes_for_preparation(votes_payload: dict[str, Any]) -> dict[str, Any]:
        alternatives: list[dict[str, Any]] = []
        for idx, raw in enumerate(list(votes_payload.get('alternatives') or []), start=1):
            if not isinstance(raw, dict):
                continue
            alt_id = str(raw.get('alternative_id') or raw.get('id') or f'alt-{idx}').strip()
            title = str(raw.get('title') or raw.get('alternative_title') or '').strip()
            if not alt_id or not title:
                continue
            alternatives.append(
                {
                    'alternative_id': alt_id,
                    'title': title,
                    'recommendation_rank': int(raw.get('recommendation_rank') or idx),
                    'recommendation_reason': str(raw.get('recommendation_reason') or '').strip(),
                }
            )
            if len(alternatives) >= 2:
                break

        return {
            'alternatives': alternatives,
            'recommended_alternative_id': str(votes_payload.get('recommended_alternative_id') or '').strip(),
        }

    @staticmethod
    def _phase4_default_preparation_items(
        *,
        rank: int,
        alternative_title: str,
    ) -> list[dict[str, Any]]:
        return [
            PreparationItem(
                id=f'alt{rank}-1',
                category='탐색',
                title=f'{alternative_title} 관련 채널 5곳 조사',
                detail='채용/프로그램 공고를 모아 요구역량과 준비 우선순위를 정리합니다.',
            ).model_dump(mode='json'),
            PreparationItem(
                id=f'alt{rank}-2',
                category='실행',
                title='2주 내 결과물 1건 제작',
                detail='관심 대안과 연결되는 미니 프로젝트 또는 포트폴리오 초안을 완성합니다.',
            ).model_dump(mode='json'),
            PreparationItem(
                id=f'alt{rank}-3',
                category='검증',
                title='현업 인터뷰 또는 멘토링 1회 진행',
                detail='실무자 피드백을 받아 준비 우선순위와 실행 계획을 조정합니다.',
            ).model_dump(mode='json'),
        ]

    @classmethod
    def _phase4_normalize_preparation_output(
        cls,
        output_json: dict[str, Any],
        *,
        votes_payload: dict[str, Any],
    ) -> dict[str, Any]:
        raw_alts = list(output_json.get('alternatives') or [])
        expected_alts = list(votes_payload.get('alternatives') or [])
        if not expected_alts:
            return {'alternatives': []}

        by_id: dict[str, dict[str, Any]] = {}
        by_title: dict[str, dict[str, Any]] = {}
        for raw in raw_alts:
            if not isinstance(raw, dict):
                continue
            rid = str(raw.get('alternative_id') or '').strip()
            rtitle = str(raw.get('alternative_title') or raw.get('title') or '').strip()
            if rid:
                by_id[rid] = raw
            if rtitle:
                by_title[rtitle] = raw

        normalized_alts: list[dict[str, Any]] = []
        for idx, expected in enumerate(expected_alts, start=1):
            alt_id = str(expected.get('alternative_id') or f'alt-{idx}').strip() or f'alt-{idx}'
            alt_title = str(expected.get('title') or '').strip() or f'대안 {idx}'
            raw = by_id.get(alt_id) or by_title.get(alt_title) or {}

            normalized_items: list[dict[str, Any]] = []
            for j, item in enumerate(list(raw.get('items') or []), start=1):
                if not isinstance(item, dict):
                    continue
                category = str(item.get('category') or '실행').strip() or '실행'
                title = re.sub(r'^\[[^\]]+\]\s*', '', str(item.get('title') or '').strip())
                detail = re.sub(r'^\[[^\]]+\]\s*', '', str(item.get('detail') or '').strip())
                if not title and not detail:
                    continue
                if not title:
                    title = f'{alt_title} 준비 실행 항목 {j}'
                if not detail:
                    detail = '실행 기준과 산출물을 함께 적어 진행합니다.'
                normalized_items.append(
                    PreparationItem(
                        id=f'{alt_id}-{j}',
                        category=category,
                        title=title,
                        detail=detail,
                    ).model_dump(mode='json')
                )
                if len(normalized_items) >= 10:
                    break
            if len(normalized_items) < 3:
                defaults = cls._phase4_default_preparation_items(
                    rank=idx,
                    alternative_title=alt_title,
                )
                for item in defaults:
                    if len(normalized_items) >= 3:
                        break
                    normalized_items.append(item)

            normalized_alts.append(
                AlternativePreparation(
                    rank=1 if idx == 1 else 2,
                    alternative_id=alt_id,
                    alternative_title=alt_title,
                    items=normalized_items,
                ).model_dump(mode='json')
            )

        return {'alternatives': normalized_alts}

    @staticmethod
    def _phase4_refine_roadmap_rows(
        rows: list[Phase4RoadmapRow],
        *,
        selected_payload: dict[str, Any],
        execution_plan_payload: dict[str, Any],
    ) -> list[Phase4RoadmapRow]:
        if not rows:
            # Keep empty when user has not provided roadmap content yet.
            # Do not auto-fill scaffolding rows.
            return []

        selected_title = ''
        selected_id = str(selected_payload.get('final_choice_id') or '').strip()
        for item in list(selected_payload.get('alternatives') or []):
            if str(item.get('alternative_id') or '').strip() == selected_id:
                selected_title = str(item.get('title') or '').strip()
                break
        if not selected_title:
            alternatives = list(selected_payload.get('alternatives') or [])
            if alternatives:
                selected_title = str(alternatives[0].get('title') or '').strip()

        plan_lines: list[str] = []
        selected_plan_text = ''
        for alt in list(execution_plan_payload.get('alternatives') or []):
            alt_id = str(alt.get('alternative_id') or '').strip()
            if selected_id and alt_id == selected_id:
                selected_plan_text = str(alt.get('plan_text') or '')
                break
        if not selected_plan_text:
            for alt in list(execution_plan_payload.get('alternatives') or []):
                selected_plan_text = str(alt.get('plan_text') or '')
                if selected_plan_text.strip():
                    break

        for raw_line in selected_plan_text.split('\n'):
            line = str(raw_line or '').strip()
            line = re.sub(r'^\-\s*', '', line).strip()
            if not line:
                continue
            line = re.sub(r'^\[[^\]]+\]\s*', '', line).strip()
            if line:
                plan_lines.append(line)

        target = selected_title or '선택 대안'
        practical_templates: list[tuple[str, str, str]] = [
            (
                f'{target} 관련 공고/프로그램 10건을 수집해 공통 요구역량을 분류한다.',
                '요구역량 매핑표(기술/경험/포트폴리오 항목)',
                '1주',
            ),
            (
                f'{target} 준비에 필요한 핵심 결과물 1개를 직접 제작한다.',
                '포트폴리오 초안 또는 실습 결과물 1건',
                '2주',
            ),
            (
                f'{target} 종사자 또는 선배 1명과 인터뷰를 진행해 진입 경로를 검증한다.',
                '인터뷰 메모 + 다음 행동 3개',
                '2~3주',
            ),
            (
                f'{target} 지원용 문서(이력서/자기소개서/프로필)를 수정하고 피드백을 반영한다.',
                '지원 문서 최종본 1세트',
                '3~4주',
            ),
        ]
        for line in plan_lines[:3]:
            short_line = line[:120]
            practical_templates.insert(
                0,
                (
                    short_line,
                    '실행 증빙(기록/산출물/스크린샷) 1건',
                    '1주',
                ),
            )

        # Keep hard replacement only for clearly unusable short/generic actions.
        hard_replace_exact_phrases = {
            '핵심 목표를 달성',
            '실행 계획을 강화',
            '체계 구축',
            '준비를 강화',
            '역량을 강화',
        }

        refined: list[Phase4RoadmapRow] = []
        template_index = 0
        for idx, row in enumerate(rows or [], start=1):
            action = str(row.action or '').strip()
            deliverable = str(row.deliverable or '').strip()
            timing = str(row.timing or '').strip()

            compact_action = re.sub(r'\s+', ' ', action).strip()
            needs_replace = (
                not action
                or len(compact_action) < 10
                or compact_action in hard_replace_exact_phrases
            )
            if needs_replace:
                tpl = practical_templates[min(template_index, len(practical_templates) - 1)]
                action, deliverable, timing = tpl
                template_index += 1
            else:
                if not deliverable:
                    deliverable = '실행 결과를 확인할 수 있는 문서/산출물 1건'
                if not timing:
                    timing = '1~2주'

            refined.append(
                Phase4RoadmapRow(
                    id=f'roadmap-{idx}',
                    action=TaskRunner._phase4_strip_internal_id_tokens(action),
                    deliverable=TaskRunner._phase4_strip_internal_id_tokens(deliverable),
                    timing=TaskRunner._phase4_normalize_timing_text(timing),
                )
            )

        # Keep roadmap concise and actionable for preparation-stage users.
        return refined[:5]

    @staticmethod
    def _phase4_rows_for_model_prompt(rows_raw: list[Any]) -> list[dict[str, str]]:
        compact: list[dict[str, str]] = []
        for raw in rows_raw:
            if not isinstance(raw, dict):
                continue
            action = TaskRunner._phase4_strip_internal_id_tokens(str(raw.get('action') or '').strip())
            deliverable = TaskRunner._phase4_strip_internal_id_tokens(str(raw.get('deliverable') or '').strip())
            timing = TaskRunner._phase4_normalize_timing_text(str(raw.get('timing') or '').strip())
            if not action and not deliverable and not timing:
                continue
            compact.append(
                {
                    'action': action,
                    'deliverable': deliverable,
                    'timing': timing,
                }
            )
        return compact[:5]

    @staticmethod
    def _phase4_strip_internal_id_tokens(text: str) -> str:
        value = str(text or '').strip()
        if not value:
            return ''
        value = re.sub(r'\broadmap[-_\s]?\d+\b[:：]?', '', value, flags=re.I)
        value = re.sub(r'\br\d+\b[:：]?', '', value, flags=re.I)
        value = re.sub(r'\s{2,}', ' ', value).strip()
        value = re.sub(r'^[\-–—,:;)\]]+\s*', '', value).strip()
        return value

    @staticmethod
    def _phase4_normalize_timing_text(timing: str) -> str:
        text = TaskRunner._phase4_strip_internal_id_tokens(timing)
        if not text:
            return '1~2주'
        # Remove explicit day-of-week details for better realism.
        text = re.sub(r'(월|화|수|목|금|토|일)(요일)?\s*[~\-]\s*(월|화|수|목|금|토|일)(요일)?', '', text)
        text = re.sub(r'\b(월|화|수|목|금|토|일)(요일)?\b', '', text)
        text = re.sub(r'[,/]+', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip(' -~')
        return text or '1~2주'

    @staticmethod
    def _is_first_person_worry(text: str) -> bool:
        t = text.strip()
        patterns = [
            r'걱정돼',
            r'걱정된다',
            r'걱정했',
            r'(내가|나는|난|저는|전|제가).{0,12}걱정',
        ]
        return any(re.search(p, t) for p in patterns)

    @staticmethod
    def _is_third_party_worry(text: str) -> bool:
        t = text.strip()
        return bool(re.search(r'(부모|엄마|아빠|가족|친구|주변).{0,12}걱정', t))

    @classmethod
    def _sanitize_phase1_snapshot(
        cls,
        merged_snapshot: dict[str, Any],
        existing_snapshot: dict[str, Any],
        user_message: str,
    ) -> dict[str, Any]:
        if not user_message:
            return merged_snapshot

        if cls._is_third_party_worry(user_message) and not cls._is_first_person_worry(user_message):
            existing_emotions = {
                str(v).strip() for v in list(existing_snapshot.get('emotions', []) or []) if str(v).strip()
            }
            new_emotions = []
            for raw in list(merged_snapshot.get('emotions', []) or []):
                emotion = str(raw).strip()
                if not emotion:
                    continue
                if emotion == '걱정' and emotion not in existing_emotions:
                    continue
                new_emotions.append(emotion)
            merged_snapshot['emotions'] = new_emotions
        return merged_snapshot

    @staticmethod
    def _phase1_clamp_core_lists(snapshot: dict[str, Any]) -> dict[str, Any]:
        # Keep normalization/dedup but do not hide user-provided items by count.
        snapshot['significant_others'] = TaskRunner._phase1_compact_significant_others(
            snapshot.get('significant_others', []),
            latest_user_message='',
        )
        snapshot['emotions'] = TaskRunner._phase1_compact_emotions(snapshot.get('emotions', []))
        for key in [
            'events',
            'significant_others',
            'emotions',
            'avoidance_behavior',
            'physical_feelings',
            'values',
            'interests',
            'skills',
            'occupational_interests',
        ]:
            deduped: list[str] = []
            seen: set[str] = set()
            for raw in list(snapshot.get(key, []) or []):
                item = ' '.join(str(raw).split()).strip()
                if not item:
                    continue
                if item in seen:
                    continue
                seen.add(item)
                deduped.append(item)
            snapshot[key] = deduped
        return snapshot

    @staticmethod
    def _phase1_compact_emotions(values: list[str] | Any) -> list[str]:
        canonical_map = {
            '불안감': '불안',
            '걱정됨': '걱정',
            '걱정된다': '걱정',
            '막막하다': '막막함',
            '답답하다': '답답함',
            '초조하다': '초조함',
            '긴장감': '긴장',
        }

        compacted: list[str] = []
        seen: set[str] = set()
        for raw in list(values or []):
            item = ' '.join(str(raw).split()).strip()
            if not item:
                continue
            canonical = canonical_map.get(item, item)
            if canonical in seen:
                continue
            seen.add(canonical)
            compacted.append(canonical)
        return compacted

    @staticmethod
    def _phase1_force_literal_significant_others(
        snapshot: dict[str, Any],
        user_message: str,
    ) -> dict[str, Any]:
        snapshot['significant_others'] = TaskRunner._phase1_compact_significant_others(
            snapshot.get('significant_others', []),
            latest_user_message=user_message,
        )
        return snapshot

    @staticmethod
    def _phase1_compact_significant_others(
        values: list[str] | Any,
        *,
        latest_user_message: str,
    ) -> list[str]:
        def norm(value: str) -> str:
            return ' '.join(str(value).split()).strip()

        raw_latest = norm(latest_user_message)
        if raw_latest:
            # For this slot, prioritize literal user wording to avoid model paraphrase expansion.
            return [raw_latest]

        incoming = [norm(v) for v in list(values or []) if norm(v)]
        if not incoming:
            return []

        deduped: list[str] = []
        seen: set[str] = set()
        for item in incoming:
            key = item.replace(' ', '')
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    @staticmethod
    def _phase2_infer_user_profile(summary: dict[str, Any], user_utterances: list[str]) -> dict[str, Any]:
        text_blob = ' '.join(
            [
                ' '.join([str(v) for v in summary.get('events', [])]),
                ' '.join([str(v) for v in summary.get('interests', [])]),
                ' '.join([str(v) for v in summary.get('occupational_interests', [])]),
                ' '.join(user_utterances),
            ]
        )
        major_track = 'unknown'
        engineering_keywords = ['공학', '컴퓨터', '소프트웨어', '코딩', '개발', '기계', '전기', '전자', '수학', '과학']
        humanities_keywords = [
            '인문',
            '문학',
            '철학',
            '역사',
            '법학',
            '법',
            '사법',
            '형사',
            '행정',
            '정책',
            '언어',
            '사회학',
            '정치',
            '교육',
            '심리',
        ]
        business_keywords = ['경영', '경제', '회계', '마케팅', '재무', '창업', '비즈니스']

        def count_keywords(keywords: list[str]) -> int:
            return sum(1 for keyword in keywords if keyword in text_blob)

        counts = {
            'engineering': count_keywords(engineering_keywords),
            'humanities': count_keywords(humanities_keywords),
            'business': count_keywords(business_keywords),
        }
        best_track, best_score = max(counts.items(), key=lambda item: item[1])
        top_count = list(counts.values()).count(best_score)
        if best_score >= 1 and (top_count == 1 or best_score >= 2):
            major_track = best_track

        return {
            'major_track': major_track,
            'keyword_counts': counts,
        }

    @staticmethod
    def _phase3_trim_line(text: str, max_len: int) -> str:
        compact = ' '.join(str(text or '').split()).strip()
        if len(compact) <= max_len:
            return compact
        return compact[:max_len].rstrip()

    @staticmethod
    def _phase3_to_note_style_line(text: str) -> str:
        line = ' '.join(str(text or '').split()).strip()
        if not line:
            return ''

        line = re.sub(r'[.!?]+$', '', line)

        replacements = [
            ('수 있습니다', '수 있음'),
            ('가능합니다', '가능함'),
            ('필요합니다', '필요함'),
            ('중요합니다', '중요함'),
            ('부담됩니다', '부담됨'),
            ('우려됩니다', '우려됨'),
        ]
        for src, dst in replacements:
            line = line.replace(src, dst)

        endings = [
            ('없습니다', '없음'),
            ('있습니다', '있음'),
            ('입니다', '임'),
            ('이에요', '임'),
            ('예요', '임'),
            ('됩니다', '됨'),
            ('합니다', '함'),
        ]
        for src, dst in endings:
            if line.endswith(src):
                line = f'{line[:-len(src)]}{dst}'.strip()
                break

        return line

    @classmethod
    def _phase3_trim_multiline(cls, text: str, max_len: int) -> str:
        lines = [cls._phase3_trim_line(line, max_len) for line in str(text or '').splitlines()]
        lines = [line for line in lines if line]
        joined = '\n'.join(lines)
        if len(joined) <= max_len:
            return joined
        return joined[:max_len].rstrip()

    @classmethod
    def _phase3_trim_multiline_note_style(cls, text: str, max_len: int) -> str:
        lines = [
            cls._phase3_trim_line(cls._phase3_to_note_style_line(line), max_len)
            for line in str(text or '').splitlines()
        ]
        lines = [line for line in lines if line]
        joined = '\n'.join(lines)
        if len(joined) <= max_len:
            return joined
        return joined[:max_len].rstrip()

    def _mock_phase4_preparation(self, votes_payload: dict[str, Any]) -> dict[str, Any]:
        alternatives = votes_payload.get('alternatives', [])[:2]
        if not alternatives:
            alternatives = [
                {'alternative_id': 'u1', 'title': '서비스 디자이너'},
                {'alternative_id': 'u2', 'title': '프로덕트 매니저'},
            ]

        output: list[dict[str, Any]] = []
        for rank, alt in enumerate(alternatives, start=1):
            items = [
                PreparationItem(
                    id=f'alt{rank}-1',
                    category='교육',
                    title=f"{alt['title']} 관련 실무 과정 수강",
                    detail='8~12주 과정으로 핵심 개념/도구를 학습하고 주차별 과제를 완료합니다.',
                ).model_dump(mode='json'),
                PreparationItem(
                    id=f'alt{rank}-2',
                    category='경험',
                    title='소규모 프로젝트 1건 완성',
                    detail='결과물을 제출 가능한 형태로 만들고 피드백 1회를 반영합니다.',
                ).model_dump(mode='json'),
                PreparationItem(
                    id=f'alt{rank}-3',
                    category='검증',
                    title='현업 인터뷰/멘토링 1회',
                    detail='준비 순서와 부족 역량을 확인해 다음 2주 계획을 보정합니다.',
                ).model_dump(mode='json'),
            ]
            output.append(
                AlternativePreparation(
                    rank=1 if rank == 1 else 2,
                    alternative_id=alt['alternative_id'],
                    alternative_title=alt['title'],
                    items=items,
                ).model_dump(mode='json')
            )
        return {'alternatives': output}
