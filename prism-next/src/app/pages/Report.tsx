import React from 'react';
import { getSessionDetail } from '@/lib/backend';
import { FileText, Target, Map as MapIcon } from 'lucide-react';

interface Artifact {
  artifact_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

interface StructuredSummary {
  events?: string[];
  significant_others?: string[];
  emotions?: string[];
  avoidance_behavior?: string[];
  physical_feelings?: string[];
  values?: string[];
  interests?: string[];
  skills?: string[];
  occupational_interests?: string[];
  decision_style?: string;
  metacognition?: {
    self_talk?: string;
    self_awareness?: string;
    control_and_monitoring?: string;
  };
}

interface FinalSelection {
  final_choice_id?: string;
  alternatives?: Array<{ alternative_id: string; title: string }>;
}

interface DecisionMatrix {
  alternatives?: Array<{
    alternative_id: string;
    alternative_title: string;
    perspective_summaries?: { self?: string; others?: string };
  }>;
}

interface ExecutionPlan {
  alternatives?: Array<{
    rank: number;
    alternative_title: string;
    plan_text?: string;
  }>;
}

interface RoadmapRows {
  rows?: Array<{
    id: string;
    action: string;
    deliverable?: string;
    timing?: string;
  }>;
}

function pickLatestArtifact<T = Record<string, unknown>>(artifacts: Artifact[], type: string): T | null {
  const found = artifacts
    .filter(item => item.artifact_type === type)
    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())[0];
  return (found?.payload as T | undefined) ?? null;
}

function compact(items?: string[]): string {
  const list = (items || []).map(v => String(v || '').trim()).filter(Boolean);
  return list.join(', ');
}

function stripInternalIdTokens(text: string): string {
  const value = String(text || '').trim();
  if (!value) return '';
  return value
    .replace(/\broadmap[-_\s]?\d+\b[:：]?/gi, '')
    .replace(/\br\d+\b[:：]?/gi, '')
    .replace(/\s{2,}/g, ' ')
    .replace(/^[\-–—,:;)\]]+\s*/, '')
    .trim();
}

function sectionSummary(summary: StructuredSummary | null): string {
  if (!summary) return '';
  const valueCount = (summary.values || []).length;
  const interestCount = (summary.interests || []).length;
  const skillCount = (summary.skills || []).length;
  if (!valueCount && !interestCount && !skillCount) return '';
  return `가치 ${valueCount}개, 흥미 ${interestCount}개, 기술 ${skillCount}개를 중심으로 자기이해가 정리되었습니다.`;
}

export default function ReportPage() {
  const [isLoading, setIsLoading] = React.useState(true);
  const [error, setError] = React.useState('');

  const [structured, setStructured] = React.useState<StructuredSummary | null>(null);
  const [selection, setSelection] = React.useState<FinalSelection | null>(null);
  const [matrix, setMatrix] = React.useState<DecisionMatrix | null>(null);
  const [executionPlan, setExecutionPlan] = React.useState<ExecutionPlan | null>(null);
  const [roadmap, setRoadmap] = React.useState<RoadmapRows | null>(null);

  React.useEffect(() => {
    let mounted = true;
    const load = async () => {
      setIsLoading(true);
      setError('');
      try {
        const detail = await getSessionDetail();
        if (!mounted) return;
        const artifacts = (detail.artifacts || []) as Artifact[];
        setStructured(
          pickLatestArtifact<StructuredSummary>(artifacts, 'phase1_structured_confirmed') ||
            pickLatestArtifact<StructuredSummary>(artifacts, 'phase1_structured'),
        );
        setSelection(pickLatestArtifact<FinalSelection>(artifacts, 'phase3_final_selection'));
        setMatrix(pickLatestArtifact<DecisionMatrix>(artifacts, 'phase3_decision_matrix'));
        setExecutionPlan(pickLatestArtifact<ExecutionPlan>(artifacts, 'phase4_execution_plan'));
        setRoadmap(pickLatestArtifact<RoadmapRows>(artifacts, 'phase4_roadmap_rows'));
      } catch (e) {
        if (!mounted) return;
        setError(e instanceof Error ? e.message : '리포트를 불러오지 못했습니다.');
      } finally {
        if (mounted) setIsLoading(false);
      }
    };
    load();
    return () => {
      mounted = false;
    };
  }, []);

  const finalChoice = React.useMemo(() => {
    if (!selection?.alternatives?.length) return null;
    return selection.alternatives.find(item => item.alternative_id === selection.final_choice_id) || null;
  }, [selection]);

  return (
    <div
      className="min-h-screen overflow-y-auto p-8"
      style={{ backgroundColor: 'var(--color-bg)', color: 'var(--color-text-primary)' }}
    >
      <div className="max-w-6xl mx-auto">
          <div className="mb-8">
            <span className="text-[13px] mb-1 block" style={{ color: 'var(--color-accent)' }}>
              Report
            </span>
            <h1 style={{ color: 'var(--color-text-primary)' }}>최종 리포트</h1>
          </div>

          {isLoading && (
            <div
              className="p-6 rounded-xl text-[14px]"
              style={{
                backgroundColor: 'var(--color-bg-card)',
                border: '1px solid var(--color-border)',
                color: 'var(--color-text-secondary)',
              }}
            >
              리포트를 생성하는 중입니다...
            </div>
          )}

          {error && !isLoading && (
            <div
              className="p-6 rounded-xl text-[14px]"
              style={{
                backgroundColor: 'rgba(239,68,68,0.1)',
                border: '1px solid rgba(239,68,68,0.35)',
                color: 'var(--color-text-primary)',
              }}
            >
              {error}
            </div>
          )}

          {!isLoading && !error && (
            <div className="space-y-6">
              <section
                className="p-6 rounded-xl"
                style={{ backgroundColor: 'var(--color-bg-card)', border: '1px solid var(--color-border)' }}
              >
                <div className="flex items-center gap-2 mb-3">
                  <FileText className="w-5 h-5" style={{ color: 'var(--color-accent)', strokeWidth: 1.5 }} />
                  <h2 style={{ color: 'var(--color-text-primary)' }}>1. 자기이해</h2>
                </div>
                <p className="text-[12px] mb-4" style={{ color: 'var(--color-text-secondary)', lineHeight: 2.05 }}>
                  {sectionSummary(structured)}
                </p>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2 text-[12px]" style={{ color: 'var(--color-text-primary)', lineHeight: 2.05 }}>
                    <p><b>사건:</b> {compact(structured?.events)}</p>
                    <p><b>주요 타인:</b> {compact(structured?.significant_others)}</p>
                    <p><b>정서:</b> {compact(structured?.emotions)}</p>
                    <p><b>회피 행동:</b> {compact(structured?.avoidance_behavior)}</p>
                    <p><b>신체 반응:</b> {compact(structured?.physical_feelings)}</p>
                  </div>
                  <div className="space-y-2 text-[12px]" style={{ color: 'var(--color-text-primary)', lineHeight: 2.05 }}>
                    <p><b>가치:</b> {compact(structured?.values)}</p>
                    <p><b>흥미:</b> {compact(structured?.interests)}</p>
                    <p><b>기술:</b> {compact(structured?.skills)}</p>
                    <p><b>직업적 흥미:</b> {compact(structured?.occupational_interests)}</p>
                    <p><b>의사결정 방식:</b> {structured?.decision_style || ''}</p>
                  </div>
                </div>
              </section>

              <section
                className="p-6 rounded-xl"
                style={{ backgroundColor: 'var(--color-bg-card)', border: '1px solid var(--color-border)' }}
              >
                <div className="flex items-center gap-2 mb-3">
                  <Target className="w-5 h-5" style={{ color: 'var(--color-accent)', strokeWidth: 1.5 }} />
                  <h2 style={{ color: 'var(--color-text-primary)' }}>2. 최종 선택 대안</h2>
                </div>
                <p className="text-[14px] mb-4" style={{ color: 'var(--color-text-primary)', lineHeight: 1.95 }}>
                  <b>최종 선택:</b> {finalChoice?.title || ''}
                </p>
                <div className="space-y-3">
                  {(matrix?.alternatives || []).map(alt => (
                    <div
                      key={alt.alternative_id}
                      className="p-4 rounded-lg"
                      style={{
                        backgroundColor: 'var(--color-bg-surface)',
                        border: alt.alternative_id === selection?.final_choice_id
                          ? '1.5px solid var(--color-accent)'
                          : '1px solid var(--color-border)',
                      }}
                    >
                      <p className="text-[14px] mb-2" style={{ color: 'var(--color-text-primary)', fontWeight: 600 }}>
                        {alt.alternative_title}
                      </p>
                      <p className="text-[13px]" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.95 }}>
                        {alt.perspective_summaries?.self || ''}
                      </p>
                      <p className="text-[13px] mt-1.5" style={{ color: 'var(--color-text-secondary)', lineHeight: 1.95 }}>
                        {alt.perspective_summaries?.others || ''}
                      </p>
                    </div>
                  ))}
                </div>
              </section>

              <section
                className="p-6 rounded-xl"
                style={{ backgroundColor: 'var(--color-bg-card)', border: '1px solid var(--color-border)' }}
              >
                <div className="flex items-center gap-2 mb-3">
                  <MapIcon className="w-5 h-5" style={{ color: 'var(--color-accent)', strokeWidth: 1.5 }} />
                  <h2 style={{ color: 'var(--color-text-primary)' }}>3. 준비 항목 및 실행 로드맵</h2>
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <div
                    className="p-4 rounded-lg"
                    style={{ backgroundColor: 'var(--color-bg-surface)', border: '1px solid var(--color-border)' }}
                  >
                    <h3 className="mb-2 text-[16px]" style={{ color: 'var(--color-text-primary)' }}>준비 항목 / 실행 계획</h3>
                    <div className="space-y-3 text-[12px]" style={{ color: 'var(--color-text-primary)', lineHeight: 2.1 }}>
                      {(executionPlan?.alternatives || []).map(alt => (
                        <div key={`plan-${alt.rank}`}>
                          <p className="mb-1 text-[12px]" style={{ fontWeight: 600 }}>{alt.rank}순위 · {alt.alternative_title}</p>
                          {(alt.plan_text || '')
                            .split('\n')
                            .map(line => line.trim())
                            .filter(Boolean)
                            .map((line, idx) => (
                              <p key={`line-${alt.rank}-${idx}`} className="mb-1 text-[12px]" style={{ color: 'var(--color-text-secondary)' }}>• {line}</p>
                            ))}
                        </div>
                      ))}
                    </div>
                  </div>

                  <div
                    className="p-4 rounded-lg"
                    style={{ backgroundColor: 'var(--color-bg-surface)', border: '1px solid var(--color-border)' }}
                  >
                    <h3 className="mb-2 text-[16px]" style={{ color: 'var(--color-text-primary)' }}>실행 로드맵</h3>
                    <div className="space-y-3 text-[12px]" style={{ color: 'var(--color-text-primary)', lineHeight: 2.1 }}>
                      {(roadmap?.rows || []).map((row, idx) => (
                        <div key={row.id || `row-${idx}`}>
                          <p className="mb-1 text-[12px]" style={{ fontWeight: 600 }}>
                            {idx + 1}. {stripInternalIdTokens(row.action)}
                          </p>
                          {row.deliverable && (
                            <p className="mb-1 text-[12px]" style={{ color: 'var(--color-text-secondary)' }}>
                              산출물: {stripInternalIdTokens(row.deliverable)}
                            </p>
                          )}
                          {row.timing && (
                            <p className="text-[12px]" style={{ color: 'var(--color-text-secondary)' }}>
                              시기: {stripInternalIdTokens(row.timing)}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </section>
            </div>
          )}
      </div>
    </div>
  );
}
