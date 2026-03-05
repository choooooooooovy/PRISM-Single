import React from 'react';
import { Layout } from '../components/Layout';
import { FooterStepNav } from '../components/FooterStepNav';
import { Phase1StructuredSummary } from '@/lib/interviewSummary';
import { getLatestArtifact, getUserErrorMessage, upsertArtifact } from '@/lib/backend';
import { FileText } from 'lucide-react';

const EMPTY_SUMMARY: Phase1StructuredSummary = {
  events: [],
  significant_others: [],
  emotions: [],
  avoidance_behavior: [],
  physical_feelings: [],
  values: [],
  interests: [],
  skills: [],
  occupational_interests: [],
  decision_style: '',
  metacognition: {
    self_talk: '',
    self_awareness: '',
    control_and_monitoring: '',
  },
};

const LIST_FIELDS: Array<{ key: keyof Phase1StructuredSummary; label: string }> = [
  { key: 'events', label: '사건' },
  { key: 'significant_others', label: '주요 타인 영향' },
  { key: 'emotions', label: '정서' },
  { key: 'avoidance_behavior', label: '회피 행동' },
  { key: 'physical_feelings', label: '신체적 느낌' },
  { key: 'values', label: '가치' },
  { key: 'interests', label: '흥미' },
  { key: 'skills', label: '기술' },
  { key: 'occupational_interests', label: '직업적 흥미' },
];

function normalizeList(value: string): string[] {
  return value
    .split('\n')
    .map(line => line.replace(/^[\-•]\s*/, '').trim())
    .filter(Boolean);
}

function listToText(items?: string[]): string {
  return (items || []).join('\n');
}

function sanitizeSummaryForSubmit(summary: Phase1StructuredSummary): Phase1StructuredSummary {
  const sanitizeList = (items: string[]) =>
    items
      .map(item => item.trim())
      .filter(Boolean);

  return {
    ...summary,
    events: sanitizeList(summary.events || []),
    significant_others: sanitizeList(summary.significant_others || []),
    emotions: sanitizeList(summary.emotions || []),
    avoidance_behavior: sanitizeList(summary.avoidance_behavior || []),
    physical_feelings: sanitizeList(summary.physical_feelings || []),
    values: sanitizeList(summary.values || []),
    interests: sanitizeList(summary.interests || []),
    skills: sanitizeList(summary.skills || []),
    occupational_interests: sanitizeList(summary.occupational_interests || []),
    decision_style: (summary.decision_style || '').trim(),
    metacognition: {
      self_talk: (summary.metacognition?.self_talk || '').trim(),
      self_awareness: (summary.metacognition?.self_awareness || '').trim(),
      control_and_monitoring: (summary.metacognition?.control_and_monitoring || '').trim(),
    },
  };
}

export default function Phase1_2SummaryReview() {
  const [summary, setSummary] = React.useState<Phase1StructuredSummary>(EMPTY_SUMMARY);
  const [isLoading, setIsLoading] = React.useState(true);
  const [hasSummary, setHasSummary] = React.useState(false);
  const [isSaving, setIsSaving] = React.useState(false);
  const [errorMessage, setErrorMessage] = React.useState('');

  React.useEffect(() => {
    let mounted = true;
    const load = async () => {
      setIsLoading(true);
      try {
        const [confirmedSummary, structuredSummary] = await Promise.all([
          getLatestArtifact<Phase1StructuredSummary>('phase1_structured_confirmed'),
          getLatestArtifact<Phase1StructuredSummary>('phase1_structured'),
        ]);
        if (!mounted) return;
        const merged = confirmedSummary || structuredSummary;
        if (!merged) {
          setHasSummary(false);
          setSummary(EMPTY_SUMMARY);
        } else {
          setHasSummary(true);
          setSummary({
            ...EMPTY_SUMMARY,
            ...merged,
            metacognition: {
              ...EMPTY_SUMMARY.metacognition,
              ...(merged.metacognition || {}),
            },
          });
        }
      } catch {
        if (!mounted) return;
        setHasSummary(false);
        setSummary(EMPTY_SUMMARY);
      } finally {
        if (mounted) setIsLoading(false);
      }
    };

    load();
    return () => {
      mounted = false;
    };
  }, []);

  const updateListField = (key: keyof Phase1StructuredSummary, value: string) => {
    setSummary(prev => ({ ...prev, [key]: normalizeList(value) }));
  };

  const updateDecisionText = (value: string) => {
    setSummary(prev => ({ ...prev, decision_style: value }));
  };

  const updateMeta = (
    key: keyof NonNullable<Phase1StructuredSummary['metacognition']>,
    value: string,
  ) => {
    setSummary(prev => ({
      ...prev,
      metacognition: {
        ...prev.metacognition,
        [key]: value,
      },
    }));
  };

  return (
    <Layout>
      <div className="flex-1 overflow-y-auto p-8" style={{ marginLeft: '260px' }}>
        <div className="max-w-6xl mx-auto">
          <div className="mb-8">
            <span className="text-[13px] mb-1 block" style={{ color: 'var(--color-accent)' }}>
              Phase 1: 자기 이해
            </span>
            <h1 className="mb-3" style={{ color: 'var(--color-text-primary)' }}>
              인터뷰 요약 확인 및 수정
            </h1>
            <div
              className="p-4 rounded-lg"
              style={{
                backgroundColor: 'rgba(255,255,255,0.02)',
                border: '1px solid var(--color-border)',
              }}
            >
              <p className="text-[14px] mb-1" style={{ color: 'var(--color-text-primary)', fontWeight: 500 }}>
                이번 단계에서 할 일
              </p>
              <p className="text-[14px] leading-relaxed" style={{ color: 'var(--color-text-secondary)' }}>
                1-1에서 정리된 자기이해 결과를 확인하고 수정한 뒤 다음 단계로 이동하세요.
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 mb-4">
            <FileText className="w-5 h-5" style={{ color: 'var(--color-accent)', strokeWidth: 1.5 }} />
            <h2 style={{ color: 'var(--color-text-primary)' }}>인터뷰 요약 확인 및 수정</h2>
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

          {isLoading && (
            <div
              className="p-6 rounded-xl text-[14px]"
              style={{
                backgroundColor: 'var(--color-bg-card)',
                border: '1px solid var(--color-border)',
                color: 'var(--color-text-secondary)',
              }}
            >
              요약 데이터를 불러오는 중입니다...
            </div>
          )}

          {!isLoading && !hasSummary && (
            <div
              className="p-6 rounded-xl text-[14px]"
              style={{
                backgroundColor: 'var(--color-bg-card)',
                border: '1px solid var(--color-border)',
                color: 'var(--color-text-secondary)',
              }}
            >
              아직 저장된 요약이 없습니다. 1-1 인터뷰를 먼저 진행해 주세요.
            </div>
          )}

          {!isLoading && hasSummary && (
            <div
              className="p-6 rounded-xl"
              style={{
                backgroundColor: 'var(--color-bg-card)',
                border: '1px solid var(--color-border)',
              }}
            >
              <div className="grid grid-cols-2 gap-4">
                {LIST_FIELDS.map(field => (
                  <div key={field.key as string}>
                    <label className="text-[13px] mb-1 block" style={{ color: 'var(--color-text-primary)', fontWeight: 500 }}>
                      {field.label}
                    </label>
                    <textarea
                      value={listToText(summary[field.key] as string[] | undefined)}
                      onChange={e => updateListField(field.key, e.target.value)}
                      rows={4}
                      className="w-full px-3 py-2.5 rounded-lg text-[13px] leading-relaxed"
                      style={{
                        backgroundColor: 'var(--color-bg-surface)',
                        border: '1px solid var(--color-border)',
                        color: 'var(--color-text-primary)',
                        resize: 'vertical',
                        outline: 'none',
                      }}
                    />
                  </div>
                ))}
              </div>

              <div className="mt-4">
                <label className="text-[13px] mb-1 block" style={{ color: 'var(--color-text-primary)', fontWeight: 500 }}>
                  의사결정 방식
                </label>
                <textarea
                  value={summary.decision_style || ''}
                  onChange={e => updateDecisionText(e.target.value)}
                  rows={3}
                  className="w-full px-3 py-2.5 rounded-lg text-[13px] leading-relaxed"
                  style={{
                    backgroundColor: 'var(--color-bg-surface)',
                    border: '1px solid var(--color-border)',
                    color: 'var(--color-text-primary)',
                    resize: 'vertical',
                    outline: 'none',
                  }}
                />
              </div>

              <div className="grid grid-cols-3 gap-4 mt-4">
                <div>
                  <label className="text-[13px] mb-1 block" style={{ color: 'var(--color-text-primary)', fontWeight: 500 }}>
                    자기 대화
                  </label>
                  <textarea
                    value={summary.metacognition?.self_talk || ''}
                    onChange={e => updateMeta('self_talk', e.target.value)}
                    rows={3}
                    className="w-full px-3 py-2.5 rounded-lg text-[13px] leading-relaxed"
                    style={{
                      backgroundColor: 'var(--color-bg-surface)',
                      border: '1px solid var(--color-border)',
                      color: 'var(--color-text-primary)',
                      resize: 'vertical',
                      outline: 'none',
                    }}
                  />
                </div>
                <div>
                  <label className="text-[13px] mb-1 block" style={{ color: 'var(--color-text-primary)', fontWeight: 500 }}>
                    자기 인식
                  </label>
                  <textarea
                    value={summary.metacognition?.self_awareness || ''}
                    onChange={e => updateMeta('self_awareness', e.target.value)}
                    rows={3}
                    className="w-full px-3 py-2.5 rounded-lg text-[13px] leading-relaxed"
                    style={{
                      backgroundColor: 'var(--color-bg-surface)',
                      border: '1px solid var(--color-border)',
                      color: 'var(--color-text-primary)',
                      resize: 'vertical',
                      outline: 'none',
                    }}
                  />
                </div>
                <div>
                  <label className="text-[13px] mb-1 block" style={{ color: 'var(--color-text-primary)', fontWeight: 500 }}>
                    조절 전략
                  </label>
                  <textarea
                    value={summary.metacognition?.control_and_monitoring || ''}
                    onChange={e => updateMeta('control_and_monitoring', e.target.value)}
                    rows={3}
                    className="w-full px-3 py-2.5 rounded-lg text-[13px] leading-relaxed"
                    style={{
                      backgroundColor: 'var(--color-bg-surface)',
                      border: '1px solid var(--color-border)',
                      color: 'var(--color-text-primary)',
                      resize: 'vertical',
                      outline: 'none',
                    }}
                  />
                </div>
              </div>
            </div>
          )}

          <FooterStepNav
            className="mt-8 flex justify-between"
            nextDisabled={isLoading || !hasSummary || isSaving}
            onBeforeNext={async () => {
              if (!hasSummary) return false;
              try {
                setIsSaving(true);
                setErrorMessage('');
                const sanitized = sanitizeSummaryForSubmit(summary);
                await upsertArtifact({
                  phase: 'phase1',
                  step: '1-2',
                  artifactType: 'phase1_structured_confirmed',
                  payload: sanitized as Record<string, unknown>,
                });
                return true;
              } catch (error) {
                setErrorMessage(
                  getUserErrorMessage(error, '요약 저장에 실패했습니다. 잠시 후 다시 시도해 주세요.'),
                );
                return false;
              } finally {
                setIsSaving(false);
              }
            }}
          />
        </div>
      </div>
    </Layout>
  );
}
