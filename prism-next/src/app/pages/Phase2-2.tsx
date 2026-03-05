import React from 'react';
import { Layout } from '../components/Layout';
import { FooterStepNav } from '../components/FooterStepNav';
import { getLatestArtifact, getUserErrorMessage, runTask } from '@/lib/backend';
import { Plus, Trash2, GripVertical } from 'lucide-react';

interface UnifiedItem {
  id: string;
  title: string;
  summary: string;
  proposer: string;
  similar?: string;
}

const MAX_ALTS = 6;
const MIN_ALTS = 3;

function createLocalItemId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `local-${Date.now()}-${Math.floor(Math.random() * 100000)}`;
}

function ensureUniqueItems(rows: UnifiedItem[]): UnifiedItem[] {
  const used = new Set<string>();
  return rows
    .map((row, index) => ({
      ...row,
      id: String(row.id || `u${index + 1}`).trim() || `u${index + 1}`,
    }))
    .filter(row => {
      if (used.has(row.id)) return false;
      used.add(row.id);
      return true;
    });
}

export default function Phase2_2AlternativeGeneration() {
  const [items, setItems] = React.useState<UnifiedItem[]>([]);
  const [isSubmittingNext, setIsSubmittingNext] = React.useState(false);
  const [submitError, setSubmitError] = React.useState('');
  const [showAddForm, setShowAddForm] = React.useState(false);
  const [newTitle, setNewTitle] = React.useState('');

  React.useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const artifact = await getLatestArtifact<{ unified_candidates?: UnifiedItem[] }>('phase2_candidates');
        if (!artifact || !mounted) return;
        setItems(ensureUniqueItems(artifact.unified_candidates || []));
      } catch {
        // no-op
      }
    };

    load();
    return () => {
      mounted = false;
    };
  }, []);

  const addItem = () => {
    if (!newTitle.trim()) return;
    setItems(prev => [
      ...prev,
      { id: createLocalItemId(), title: newTitle.trim(), summary: '', proposer: '직접 추가' },
    ]);
    setNewTitle('');
    setShowAddForm(false);
  };

  const deleteItem = (id: string) => {
    setItems(prev => prev.filter(i => i.id !== id));
  };

  const updateTitle = (id: string, title: string) => {
    setItems(prev => prev.map(i => (i.id === id ? { ...i, title } : i)));
  };

  return (
    <Layout>
      <div className="flex-1 overflow-y-auto p-8" style={{ marginLeft: '260px' }}>
        <div className="max-w-6xl mx-auto">
          <div className="mb-6">
            <span className="text-[13px] mb-1 block" style={{ color: 'var(--color-accent)' }}>
              Phase 2: 직업 탐색
            </span>
            <h1 className="mb-3" style={{ color: 'var(--color-text-primary)' }}>
              대안 생성/정리
            </h1>
            <div
              className="p-4 rounded-lg"
              style={{ backgroundColor: 'rgba(255,255,255,0.02)', border: '1px solid var(--color-border)' }}
            >
              <p className="text-[14px] mb-1" style={{ color: 'var(--color-text-primary)', fontWeight: 500 }}>
                이번 단계에서 할 일
              </p>
              <p className="text-[14px] leading-relaxed" style={{ color: 'var(--color-text-secondary)' }}>
                탐색 결과를 바탕으로 비교할 대안을 직접 편집·확정하세요.
                <br />
                이 화면의 리스트를 다음 단계 Benefit/Cost 비교에 사용합니다.
              </p>
            </div>
          </div>

          {submitError && (
            <div
              className="mb-4 px-4 py-3 rounded-lg text-[13px]"
              style={{
                backgroundColor: 'rgba(239,68,68,0.1)',
                border: '1px solid rgba(239,68,68,0.35)',
                color: 'var(--color-text-primary)',
              }}
            >
              {submitError}
            </div>
          )}

          <div
            className="flex items-center justify-between px-5 py-3 rounded-lg mb-6"
            style={{ backgroundColor: 'rgba(255,31,86,0.06)', border: '1px solid rgba(255,31,86,0.12)' }}
          >
            <div>
              <span className="text-[14px] block" style={{ color: 'var(--color-text-primary)', fontWeight: 500 }}>
                최소 3개 대안을 남겨주세요.
                <span style={{ color: 'var(--color-text-secondary)', fontWeight: 400 }}> (권장: 6개)</span>
              </span>
              <span className="text-[12px]" style={{ color: 'var(--color-text-secondary)' }}>
                다음 단계에서 대안별 Benefit/Cost 비교를 진행합니다.
              </span>
            </div>
            <span
              className="text-[15px] px-3 py-1 rounded-full"
              style={{
                backgroundColor:
                  items.length >= MIN_ALTS && items.length <= MAX_ALTS
                    ? 'rgba(34,197,94,0.12)'
                    : 'rgba(255,31,86,0.12)',
                color:
                  items.length >= MIN_ALTS && items.length <= MAX_ALTS
                    ? 'var(--color-benefits)'
                    : 'var(--color-accent)',
                fontWeight: 600,
              }}
            >
              {items.length}/{MAX_ALTS}
            </span>
          </div>

          <div className="mb-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-[18px]" style={{ color: 'var(--color-text-primary)' }}>
                통합 후보 리스트
              </h2>
              {items.length < MAX_ALTS && (
                <button
                  onClick={() => setShowAddForm(true)}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-[13px] transition-colors"
                  style={{ backgroundColor: 'var(--color-accent)', color: 'var(--color-text-primary)' }}
                >
                  <Plus className="w-4 h-4" style={{ strokeWidth: 1.5 }} />
                  항목 추가
                </button>
              )}
            </div>

            {showAddForm && (
              <div
                className="p-4 rounded-lg mb-4"
                style={{ backgroundColor: 'var(--color-bg-card)', border: '1px solid var(--color-border)' }}
              >
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={newTitle}
                    onChange={e => setNewTitle(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && addItem()}
                    placeholder="대안명 입력..."
                    className="flex-1 px-3 py-2 rounded-lg text-[14px]"
                    style={{
                      backgroundColor: 'var(--color-bg-surface)',
                      border: '1px solid var(--color-border)',
                      color: 'var(--color-text-primary)',
                      outline: 'none',
                    }}
                    autoFocus
                  />
                  <button
                    onClick={addItem}
                    className="px-4 py-2 rounded-lg text-[13px]"
                    style={{ backgroundColor: 'var(--color-accent)', color: 'var(--color-text-primary)' }}
                  >
                    추가
                  </button>
                  <button
                    onClick={() => {
                      setShowAddForm(false);
                      setNewTitle('');
                    }}
                    className="px-4 py-2 rounded-lg text-[13px]"
                    style={{
                      backgroundColor: 'var(--color-bg-surface)',
                      color: 'var(--color-text-secondary)',
                      border: '1px solid var(--color-border)',
                    }}
                  >
                    취소
                  </button>
                </div>
              </div>
            )}

            <div className="space-y-2">
              {items.map((item, index) => (
                <div
                  key={`${item.id}-${index}`}
                  className="flex items-center gap-3 px-4 py-3 rounded-lg transition-all"
                  style={{
                    backgroundColor: 'var(--color-bg-card)',
                    border: '1px solid var(--color-border)',
                    boxShadow: 'var(--shadow-card)',
                  }}
                >
                  <GripVertical
                    className="w-4 h-4 flex-shrink-0"
                    style={{ color: 'var(--color-text-secondary)', opacity: 0.35, strokeWidth: 1.5 }}
                  />
                  <span
                    className="w-6 h-6 rounded-full flex items-center justify-center flex-shrink-0 text-[12px]"
                    style={{
                      backgroundColor: 'var(--color-bg-surface)',
                      color: 'var(--color-text-secondary)',
                      border: '1px solid var(--color-border)',
                      fontWeight: 600,
                    }}
                  >
                    {index + 1}
                  </span>

                  <input
                    type="text"
                    value={item.title}
                    onChange={e => updateTitle(item.id, e.target.value)}
                    className="flex-1 bg-transparent text-[15px] outline-none"
                    style={{ color: 'var(--color-text-primary)', fontWeight: 500 }}
                  />

                  <span
                    className="text-[11px] flex-shrink-0 px-1.5 py-0.5 rounded"
                    style={{
                      color: 'var(--color-text-secondary)',
                      backgroundColor: 'var(--color-bg-surface)',
                      border: '1px solid var(--color-border)',
                    }}
                  >
                    {item.proposer || 'AI 제안'}
                  </span>

                  <button
                    onClick={() => deleteItem(item.id)}
                    className="p-1 flex-shrink-0 rounded transition-colors"
                    style={{ color: 'var(--color-text-secondary)', opacity: 0.5 }}
                  >
                    <Trash2 className="w-3.5 h-3.5" style={{ strokeWidth: 1.5 }} />
                  </button>
                </div>
              ))}
            </div>
          </div>

          <FooterStepNav
            className="mt-6 flex justify-between"
            nextDisabled={items.length < MIN_ALTS || isSubmittingNext}
            onBeforeNext={async () => {
              setIsSubmittingNext(true);
              setSubmitError('');
              try {
                await runTask('phase3_generate_comments_and_drafts', {
                  candidates: {
                    unified_candidates: items.map(i => ({
                      id: i.id,
                      title: i.title,
                      summary: i.summary || '',
                      proposer: i.proposer || 'AI 제안',
                      similar: i.similar,
                    })),
                  },
                });
                return true;
              } catch (error) {
                setSubmitError(getUserErrorMessage(error, '다음 단계 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.'));
                return false;
              } finally {
                setIsSubmittingNext(false);
              }
            }}
          />
        </div>
      </div>
    </Layout>
  );
}
