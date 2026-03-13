import React from 'react';
import { Layout } from '../components/Layout';
import { FooterStepNav } from '../components/FooterStepNav';
import { getLatestArtifact, getUserErrorMessage, runTask, upsertArtifact } from '@/lib/backend';
import { Sparkles } from 'lucide-react';

type Perspective = 'self' | 'others';

const PERSPECTIVES: Array<{ id: Perspective; label: string; description: string }> = [
  { id: 'self', label: '자신', description: '이 대안이 나의 가치·성장·실행 가능성에 미치는 영향' },
  { id: 'others', label: '주요 타인', description: '가족/친구/동료 등 가까운 관계에 미치는 영향' },
];

interface DraftCellPayload {
  perspective: Perspective;
  benefits?: string;
  costs?: string;
  benefit_comments?: string[];
  cost_comments?: string[];
}

interface AlternativePayload {
  alternative_id: string;
  alternative_title: string;
  cells: DraftCellPayload[];
}

interface CellEditorState {
  benefitHints: string[];
  costHints: string[];
  userBenefits: string;
  userCosts: string;
}

type AltCellState = Record<Perspective, CellEditorState>;
type AltPerspectiveSummary = Record<Perspective, string>;

function emptyCellState(): CellEditorState {
  return {
    benefitHints: [],
    costHints: [],
    userBenefits: '',
    userCosts: '',
  };
}

function splitClauses(text: string): string[] {
  return text
    .split(/\n|[.!?。]|(?:\s*\/\s*)|(?:\s*;\s*)/g)
    .map(line => line.replace(/\s+/g, ' ').trim())
    .filter(Boolean);
}

function summarizePerspective(benefitText: string, costText: string): string {
  const benefits = splitClauses(benefitText).slice(0, 3);
  const costs = splitClauses(costText).slice(0, 3);
  const lines: string[] = [];
  if (benefits.length) lines.push(`Benefit: ${benefits.join(' / ')}`);
  if (costs.length) lines.push(`Cost: ${costs.join(' / ')}`);
  return lines.join('\n');
}

export default function Phase3_1BenefitCost() {
  const [alternatives, setAlternatives] = React.useState<Array<{ id: string; title: string }>>([]);
  const [selectedAltId, setSelectedAltId] = React.useState<string>('');
  const [stateByAlt, setStateByAlt] = React.useState<Record<string, AltCellState>>({});
  const [summaryByAlt, setSummaryByAlt] = React.useState<Record<string, AltPerspectiveSummary>>({});
  const [isSubmittingNext, setIsSubmittingNext] = React.useState(false);
  const [errorMessage, setErrorMessage] = React.useState('');

  React.useEffect(() => {
    let mounted = true;

    const load = async () => {
      try {
        const [draftArtifact, candidateArtifact] = await Promise.all([
          getLatestArtifact<{ alternatives?: AlternativePayload[] }>('phase3_comments_drafts'),
          getLatestArtifact<{ unified_candidates?: Array<{ id: string; title: string }> }>('phase2_candidates'),
        ]);
        if (!mounted) return;

        const sourceAlternatives: AlternativePayload[] = draftArtifact?.alternatives?.length
          ? draftArtifact.alternatives
          : (candidateArtifact?.unified_candidates || []).slice(0, 3).map(item => ({
            alternative_id: item.id,
            alternative_title: item.title,
            cells: PERSPECTIVES.map(p => ({
              perspective: p.id,
              benefits: '',
              costs: '',
              benefit_comments: [],
              cost_comments: [],
            })),
          }));

        const nextAlternatives = sourceAlternatives.map(item => ({
          id: item.alternative_id,
          title: item.alternative_title,
        }));

        const nextState: Record<string, AltCellState> = {};

        sourceAlternatives.forEach(item => {
          const perPerspective = {
            self: emptyCellState(),
            others: emptyCellState(),
          } as AltCellState;

          item.cells.forEach(cell => {
            const benefitHints = (cell.benefit_comments || []).map(v => String(v || '').trim()).filter(Boolean);
            const costHints = (cell.cost_comments || []).map(v => String(v || '').trim()).filter(Boolean);
            const benefitDraft = String(cell.benefits || '').trim();
            const costDraft = String(cell.costs || '').trim();
            perPerspective[cell.perspective] = {
              benefitHints,
              costHints,
              userBenefits: benefitDraft || benefitHints.join('\n'),
              userCosts: costDraft || costHints.join('\n'),
            };
          });

          nextState[item.alternative_id] = perPerspective;
        });

        setAlternatives(nextAlternatives);
        setStateByAlt(nextState);
        const nextSummaries: Record<string, AltPerspectiveSummary> = {};
        nextAlternatives.forEach(alt => {
          nextSummaries[alt.id] = { self: '', others: '' };
        });
        setSummaryByAlt(nextSummaries);
        if (nextAlternatives[0]) setSelectedAltId(nextAlternatives[0].id);
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

  const updateCellState = (
    altId: string,
    perspective: Perspective,
    updater: (prev: CellEditorState) => CellEditorState,
  ) => {
    setStateByAlt(prev => ({
      ...prev,
      [altId]: {
        ...prev[altId],
        [perspective]: updater(prev[altId]?.[perspective] || emptyCellState()),
      },
    }));
  };

  const selectedAltState = stateByAlt[selectedAltId];

  const generatePerspectiveSummary = (altId: string, perspective: Perspective) => {
    const cell = stateByAlt[altId]?.[perspective];
    if (!cell) return;
    const compact = summarizePerspective(cell.userBenefits, cell.userCosts);
    setSummaryByAlt(prev => ({
      ...prev,
      [altId]: {
        ...(prev[altId] || { self: '', others: '' }),
        [perspective]: compact,
      },
    }));
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
              대안별 Benefit/Cost 표 작성
            </h1>
            <div
              className="p-4 rounded-lg"
              style={{ backgroundColor: 'rgba(255,255,255,0.02)', border: '1px solid var(--color-border)' }}
            >
              <p className="text-[14px] mb-1" style={{ color: 'var(--color-text-primary)', fontWeight: 500 }}>
                이번 단계에서 할 일
              </p>
              <p className="text-[14px] leading-relaxed" style={{ color: 'var(--color-text-secondary)' }}>
                각 대안의 자신/주요 타인 관점 Benefit·Cost를 정리하고, 요약을 생성해 비교 준비를 완료하세요.
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

          <div className="flex flex-wrap gap-2 mb-6">
            {alternatives.map(alt => (
              <button
                key={alt.id}
                type="button"
                onClick={() => setSelectedAltId(alt.id)}
                className="px-4 py-2 rounded-lg text-[13px]"
                style={{
                  backgroundColor: selectedAltId === alt.id ? 'var(--color-accent)' : 'var(--color-bg-card)',
                  color: selectedAltId === alt.id ? '#fff' : 'var(--color-text-primary)',
                  border: selectedAltId === alt.id ? 'none' : '1px solid var(--color-border)',
                }}
              >
                {alt.title}
              </button>
            ))}
          </div>

          {selectedAltState && (
            <div className="space-y-5">
              {PERSPECTIVES.map(perspective => {
                const cell = selectedAltState[perspective.id] || emptyCellState();

                return (
                  <div
                    key={perspective.id}
                    className="p-5 rounded-xl"
                    style={{ backgroundColor: 'var(--color-bg-card)', border: '1px solid var(--color-border)' }}
                  >
                    <div className="mb-4">
                      <h3 style={{ color: 'var(--color-text-primary)' }}>{perspective.label}</h3>
                      <p className="text-[13px]" style={{ color: 'var(--color-text-secondary)' }}>
                        {perspective.description}
                      </p>
                    </div>

                    <div className="grid grid-cols-2 gap-5">
                      {([
                        {
                          field: 'benefit' as const,
                          title: 'Benefit',
                          hints: cell.benefitHints,
                          userText: cell.userBenefits,
                        },
                        {
                          field: 'cost' as const,
                          title: 'Cost',
                          hints: cell.costHints,
                          userText: cell.userCosts,
                        },
                      ]).map(section => (
                        <div
                          key={section.field}
                          className="p-4 rounded-lg"
                          style={{ backgroundColor: 'var(--color-bg-surface)', border: '1px solid var(--color-border)' }}
                        >
                          <div className="flex items-center gap-2 mb-3">
                            <div className="flex items-center gap-2">
                              <Sparkles className="w-4 h-4" style={{ color: 'var(--color-accent)', strokeWidth: 1.5 }} />
                              <span style={{ color: 'var(--color-text-primary)', fontWeight: 600 }}>{section.title}</span>
                            </div>
                          </div>
                          <p className="text-[12px] mb-2" style={{ color: 'var(--color-text-secondary)' }}>
                            생성된 초안이 입력되어 있습니다. 직접 수정할 수 있습니다.
                          </p>

                          <textarea
                            value={section.userText}
                            onChange={e =>
                              updateCellState(selectedAltId, perspective.id, prev =>
                                section.field === 'benefit'
                                  ? { ...prev, userBenefits: e.target.value }
                                  : { ...prev, userCosts: e.target.value },
                              )
                            }
                            rows={5}
                            placeholder="직접 작성할 내용을 입력하세요..."
                            className="w-full px-3 py-2 rounded-lg text-[13px]"
                            style={{
                              backgroundColor: 'rgba(255,31,86,0.03)',
                              border: '1px solid rgba(255,31,86,0.25)',
                              boxShadow: 'inset 0 0 0 1px rgba(255,31,86,0.08)',
                              color: 'var(--color-text-primary)',
                              resize: 'vertical',
                              outline: 'none',
                              lineHeight: 1.72,
                            }}
                          />
                        </div>
                      ))}
                    </div>

                    <div
                      className="mt-4 p-4 rounded-lg"
                      style={{ backgroundColor: 'var(--color-bg-surface)', border: '1px solid var(--color-border)' }}
                    >
                      <div className="flex items-center justify-between mb-2">
                        <h4 style={{ color: 'var(--color-text-primary)' }}>{perspective.label} 요약</h4>
                        <button
                          type="button"
                          onClick={() => generatePerspectiveSummary(selectedAltId, perspective.id)}
                          className="px-3 py-1.5 rounded-lg text-[12px]"
                          style={{
                            backgroundColor: 'var(--color-accent)',
                            color: '#fff',
                          }}
                        >
                          요약 생성
                        </button>
                      </div>
                      <p className="text-[13px] leading-relaxed whitespace-pre-line" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.75 }}>
                        {summaryByAlt[selectedAltId]?.[perspective.id] || 'Benefit/Cost 내용을 정리한 뒤 요약을 생성하세요.'}
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          <FooterStepNav
            className="mt-8 flex justify-between"
            onBeforeNext={async () => {
              setIsSubmittingNext(true);
              setErrorMessage('');
              try {
                const payload = {
                  alternatives: alternatives.map(alt => {
                    const altState = stateByAlt[alt.id] || { self: emptyCellState(), others: emptyCellState() };
                    return {
                      alternative_id: alt.id,
                      alternative_title: alt.title,
                      cells: PERSPECTIVES.map(p => ({
                        perspective: p.id,
                        benefits: altState[p.id].userBenefits,
                        costs: altState[p.id].userCosts,
                        benefit_comments: altState[p.id].benefitHints,
                        cost_comments: altState[p.id].costHints,
                      })),
                      perspective_summaries: summaryByAlt[alt.id] || { self: '', others: '' },
                    };
                  }),
                };

                await upsertArtifact({
                  phase: 'phase3',
                  step: '3-1',
                  artifactType: 'phase3_decision_matrix',
                  payload: payload as Record<string, unknown>,
                });

                await runTask('phase3_generate_votes', { drafts: payload });
                return true;
              } catch (error) {
                setErrorMessage(getUserErrorMessage(error, '다음 단계 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.'));
                return false;
              } finally {
                setIsSubmittingNext(false);
              }
            }}
            nextDisabled={isSubmittingNext || alternatives.length === 0}
          />
        </div>
      </div>
    </Layout>
  );
}
