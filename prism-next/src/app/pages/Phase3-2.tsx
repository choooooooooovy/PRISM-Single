import React from 'react';
import { Layout } from '../components/Layout';
import { FooterStepNav } from '../components/FooterStepNav';
import { getLatestArtifact, getUserErrorMessage, runTask, upsertArtifact } from '@/lib/backend';
import { CheckCircle2, RotateCcw } from 'lucide-react';

type Perspective = 'self' | 'others';

interface VoteAlternative {
  alternative_id: string;
  title: string;
  recommendation_rank: number;
  recommendation_reason?: string;
}

interface MatrixCell {
  perspective: Perspective;
  benefits: string;
  costs: string;
}

interface MatrixAlternative {
  alternative_id: string;
  alternative_title: string;
  cells: MatrixCell[];
  perspective_summaries?: {
    self?: string;
    others?: string;
  };
}

interface ExploreCard {
  id?: string;
  title?: string;
  job_title?: string;
  tasks: string;
  work_environment?: string;
  outlook?: string;
  outlook_salary?: string;
}

interface PerspectiveSummaries {
  self: string;
  others: string;
}

const PERSPECTIVE_LABEL: Record<Perspective, string> = {
  self: '자신',
  others: '주요 타인',
};

export default function Phase3_2Prioritization() {
  const [alternatives, setAlternatives] = React.useState<VoteAlternative[]>([]);
  const [matrixByAlt, setMatrixByAlt] = React.useState<Record<string, MatrixCell[]>>({});
  const [summaryByAlt, setSummaryByAlt] = React.useState<Record<string, string>>({});
  const [perspectiveSummaryByAlt, setPerspectiveSummaryByAlt] = React.useState<Record<string, PerspectiveSummaries>>({});
  const [exploreByAlt, setExploreByAlt] = React.useState<
    Record<string, { tasks: string; environment: string; outlook: string }>
  >({});
  const [selectedIds, setSelectedIds] = React.useState<string[]>([]);
  const [finalChoiceId, setFinalChoiceId] = React.useState<string | null>(null);
  const [compareMode, setCompareMode] = React.useState(false);
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const [errorMessage, setErrorMessage] = React.useState('');

  React.useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const [votesArtifact, matrixArtifact, candidatesArtifact, exploreArtifact] = await Promise.all([
          getLatestArtifact<{ alternatives?: VoteAlternative[]; recommended_alternative_id?: string }>('phase3_votes'),
          getLatestArtifact<{ alternatives?: MatrixAlternative[] }>('phase3_decision_matrix'),
          getLatestArtifact<{ unified_candidates?: Array<{ id: string; title: string; summary: string }> }>('phase2_candidates'),
          getLatestArtifact<{ cards?: ExploreCard[] }>('phase2_explore_cards'),
        ]);
        if (!mounted) return;

        const voteAlts = (votesArtifact?.alternatives || []).slice().sort((a, b) => a.recommendation_rank - b.recommendation_rank);
        setAlternatives(voteAlts);

        const matrixMap: Record<string, MatrixCell[]> = {};
        const matrixSummaryMap: Record<string, string> = {};
        const perspectiveSummaryMap: Record<string, PerspectiveSummaries> = {};
        (matrixArtifact?.alternatives || []).forEach(item => {
          matrixMap[item.alternative_id] = item.cells || [];
          const selfSummary = item.perspective_summaries?.self?.trim() || '';
          const othersSummary = item.perspective_summaries?.others?.trim() || '';
          const merged = [selfSummary, othersSummary].filter(Boolean).join(' / ');
          if (merged) matrixSummaryMap[item.alternative_id] = merged;
          perspectiveSummaryMap[item.alternative_id] = {
            self: selfSummary,
            others: othersSummary,
          };
        });
        setMatrixByAlt(matrixMap);
        setPerspectiveSummaryByAlt(perspectiveSummaryMap);

        const summaryMap: Record<string, string> = {};
        (candidatesArtifact?.unified_candidates || []).forEach(item => {
          summaryMap[item.id] = item.summary;
        });
        Object.entries(matrixSummaryMap).forEach(([key, value]) => {
          summaryMap[key] = value;
        });
        setSummaryByAlt(summaryMap);

        const exploreCards = (exploreArtifact?.cards || []) as ExploreCard[];

        const exploreMap: Record<string, { tasks: string; environment: string; outlook: string }> = {};
        voteAlts.forEach(alt => {
          const match = exploreCards.find(card => (card.title || card.job_title) === alt.title);
          if (match) {
            exploreMap[alt.alternative_id] = {
              tasks: match.tasks,
              environment: match.work_environment || '',
              outlook: match.outlook || match.outlook_salary || '',
            };
          }
        });
        setExploreByAlt(exploreMap);
      } catch {
        if (!mounted) return;
        setAlternatives([]);
      }
    };
    load();
    return () => {
      mounted = false;
    };
  }, []);

  const toggleAlternative = (alternativeId: string) => {
    setSelectedIds(prev => {
      if (prev.includes(alternativeId)) {
        const next = prev.filter(id => id !== alternativeId);
        if (finalChoiceId === alternativeId) setFinalChoiceId(null);
        return next;
      }
      if (prev.length >= 2) return prev;
      return [...prev, alternativeId];
    });
  };

  const selectedAlternatives = selectedIds
    .map(id => alternatives.find(alt => alt.alternative_id === id))
    .filter((value): value is VoteAlternative => Boolean(value));

  const showCompareOnly = compareMode;

  const toFallbackPerspectiveSummary = (altId: string, perspective: Perspective): string => {
    const cells = matrixByAlt[altId] || [];
    const cell = cells.find(item => item.perspective === perspective);
    const benefits = (cell?.benefits || '').trim();
    const costs = (cell?.costs || '').trim();
    const lines: string[] = [];
    if (benefits) lines.push(`Benefit: ${benefits}`);
    if (costs) lines.push(`Cost: ${costs}`);
    return lines.join('\n');
  };

  return (
    <Layout>
      <div className="flex-1 overflow-y-auto p-8" style={{ marginLeft: '260px' }}>
        <div className="max-w-6xl mx-auto">
          <div className="mb-6">
            <span className="text-[13px] mb-1 block" style={{ color: 'var(--color-accent)' }}>
              Phase 3: 우선순위 결정
            </span>
            <h1 className="mb-3" style={{ color: 'var(--color-text-primary)' }}>
              최종 2개 대안 선택 및 비교
            </h1>
            <div
              className="p-4 rounded-lg"
              style={{ backgroundColor: 'rgba(255,255,255,0.02)', border: '1px solid var(--color-border)' }}
            >
              <p className="text-[14px] mb-1" style={{ color: 'var(--color-text-primary)', fontWeight: 500 }}>
                이번 단계에서 할 일
              </p>
              <p className="text-[14px] leading-relaxed" style={{ color: 'var(--color-text-secondary)' }}>
                먼저 대안 2개를 고른 뒤, 두 대안을 나란히 비교해 최종 1개를 선택하세요.
              </p>
            </div>
          </div>

          {errorMessage && (
            <div
              className="mb-4 px-4 py-3 rounded-lg text-[13px]"
              style={{
                backgroundColor: 'rgba(239,68,68,0.1)',
                border: '1px solid rgba(239,68,68,0.35)',
                color: 'var(--color-text-primary)',
              }}
            >
              {errorMessage}
            </div>
          )}

          {!showCompareOnly && (
            <div className="space-y-4 mb-6">
              {alternatives.map(alt => {
                const isSelected = selectedIds.includes(alt.alternative_id);
                const explore = exploreByAlt[alt.alternative_id];
                return (
                  <button
                    key={alt.alternative_id}
                    type="button"
                    onClick={() => toggleAlternative(alt.alternative_id)}
                    className="w-full text-left p-5 rounded-xl"
                    style={{
                      backgroundColor: 'var(--color-bg-card)',
                      border: isSelected ? '1.5px solid var(--color-accent)' : '1px solid var(--color-border)',
                    }}
                  >
                    <div className="flex items-start justify-between gap-3 mb-2">
                      <h3 className="text-[18px]" style={{ color: 'var(--color-text-primary)', lineHeight: 1.45 }}>{alt.title}</h3>
                      {isSelected && <CheckCircle2 className="w-4 h-4" style={{ color: 'var(--color-accent)' }} />}
                    </div>
                    <p className="text-[14px] leading-relaxed" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.75 }}>
                      {summaryByAlt[alt.alternative_id] || alt.recommendation_reason || '이 대안의 핵심 특징을 비교해보세요.'}
                    </p>
                    {explore && (
                      <div className="mt-3 text-[13px] leading-relaxed" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.72 }}>
                        <div><b style={{ color: 'var(--color-text-primary)' }}>하는 일:</b> {explore.tasks}</div>
                        <div><b style={{ color: 'var(--color-text-primary)' }}>근무 환경:</b> {explore.environment}</div>
                      </div>
                    )}
                  </button>
                );
              })}
            </div>
          )}

          {showCompareOnly && (
            <>
              <div className="flex items-center justify-between mb-4">
                <h2 style={{ color: 'var(--color-text-primary)' }}>선택된 2개 대안 비교</h2>
                <button
                  type="button"
                  onClick={() => {
                    setCompareMode(false);
                    setFinalChoiceId(null);
                  }}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-[12px]"
                  style={{ backgroundColor: 'var(--color-bg-surface)', border: '1px solid var(--color-border)', color: 'var(--color-text-secondary)' }}
                >
                  <RotateCcw className="w-3.5 h-3.5" /> 다시 선택
                </button>
              </div>
              <div className="grid grid-cols-2 gap-5 mb-6">
                {selectedAlternatives.map(alt => {
                  const explore = exploreByAlt[alt.alternative_id];
                  const isFinal = finalChoiceId === alt.alternative_id;
                  return (
                    <div
                      key={`compare-${alt.alternative_id}`}
                      className="p-5 rounded-xl"
                      style={{ backgroundColor: 'var(--color-bg-card)', border: isFinal ? '1.5px solid var(--color-accent)' : '1px solid var(--color-border)' }}
                    >
                      <div className="flex items-start justify-between gap-3 mb-3">
                        <div className="flex-1 min-w-0">
                          <h3 className="text-[18px]" style={{ color: 'var(--color-text-primary)', lineHeight: 1.42 }}>{alt.title}</h3>
                          <p className="text-[14px] mt-1 leading-relaxed" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.72 }}>
                            {summaryByAlt[alt.alternative_id] || alt.recommendation_reason || '요약 정보'}
                          </p>
                        </div>
                        <button
                          type="button"
                          onClick={() => setFinalChoiceId(alt.alternative_id)}
                          className="px-4 py-2 rounded-lg text-[13px] whitespace-nowrap self-start"
                          style={{
                            backgroundColor: isFinal ? 'var(--color-accent)' : 'var(--color-bg-surface)',
                            color: isFinal ? '#fff' : 'var(--color-text-secondary)',
                            border: isFinal ? 'none' : '1px solid var(--color-border)',
                          }}
                        >
                          {isFinal ? '최종 선택됨' : '최종 선택'}
                        </button>
                      </div>

                      {explore && (
                        <div className="mb-4 text-[13px] leading-relaxed" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.72 }}>
                          <div><b style={{ color: 'var(--color-text-primary)' }}>하는 일:</b> {explore.tasks}</div>
                          <div><b style={{ color: 'var(--color-text-primary)' }}>근무 환경:</b> {explore.environment}</div>
                          <div><b style={{ color: 'var(--color-text-primary)' }}>전망:</b> {explore.outlook}</div>
                        </div>
                      )}

                      <div className="space-y-4">
                        {(['self', 'others'] as Perspective[]).map(perspective => (
                          <div
                            key={`${alt.alternative_id}-${perspective}`}
                            className="p-4 rounded-lg"
                            style={{ backgroundColor: 'var(--color-bg-surface)', border: '1px solid var(--color-border)' }}
                          >
                            <p className="text-[13px] mb-1.5" style={{ color: 'var(--color-text-primary)', fontWeight: 600 }}>
                              {PERSPECTIVE_LABEL[perspective]}
                            </p>
                            <p className="text-[13px] leading-relaxed whitespace-pre-line" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.78 }}>
                              {perspectiveSummaryByAlt[alt.alternative_id]?.[perspective] ||
                                toFallbackPerspectiveSummary(alt.alternative_id, perspective) ||
                                '요약 정보가 없습니다.'}
                            </p>
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          )}

          <FooterStepNav
            className="flex justify-between"
            nextLabel={showCompareOnly ? '다음 단계' : '비교하기'}
            nextDisabled={
              isSubmitting ||
              (!showCompareOnly && selectedIds.length !== 2) ||
              (showCompareOnly && !finalChoiceId)
            }
            onBeforeNext={async () => {
              if (!showCompareOnly) {
                if (selectedIds.length !== 2) {
                  setErrorMessage('먼저 비교할 대안 2개를 선택해 주세요.');
                  return false;
                }
                setErrorMessage('');
                setCompareMode(true);
                return false;
              }
              if (!finalChoiceId) return false;
              setErrorMessage('');
              setIsSubmitting(true);
              try {
                const first = alternatives.find(alt => alt.alternative_id === finalChoiceId);
                const second = selectedAlternatives.find(alt => alt.alternative_id !== finalChoiceId);
                if (!first || !second) return false;

                const selectedPayload = {
                  selected_alternative_ids: [first.alternative_id, second.alternative_id],
                  final_choice_id: first.alternative_id,
                  alternatives: [first, second],
                  matrix: {
                    [first.alternative_id]: matrixByAlt[first.alternative_id] || [],
                    [second.alternative_id]: matrixByAlt[second.alternative_id] || [],
                  },
                };

                await upsertArtifact({
                  phase: 'phase3',
                  step: '3-2',
                  artifactType: 'phase3_final_selection',
                  payload: selectedPayload as Record<string, unknown>,
                });

                await runTask('phase4_generate_preparation', {
                  votes: {
                    alternatives: [
                      {
                        alternative_id: first.alternative_id,
                        title: first.title,
                        recommendation_rank: 1,
                        recommendation_reason: first.recommendation_reason || '',
                      },
                      {
                        alternative_id: second.alternative_id,
                        title: second.title,
                        recommendation_rank: 2,
                        recommendation_reason: second.recommendation_reason || '',
                      },
                    ],
                    recommended_alternative_id: first.alternative_id,
                  },
                });
                return true;
              } catch (error) {
                setErrorMessage(getUserErrorMessage(error, '다음 단계로 이동하지 못했습니다.'));
                return false;
              } finally {
                setIsSubmitting(false);
              }
            }}
          />
        </div>
      </div>
    </Layout>
  );
}
