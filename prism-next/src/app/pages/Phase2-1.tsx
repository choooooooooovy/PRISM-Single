import React from 'react';
import { Layout } from '../components/Layout';
import { ContextPanel } from '../components/RightPanel';
import { FooterStepNav } from '../components/FooterStepNav';
import { getLatestArtifact, getMessagesByStep, getUserErrorMessage, runTask } from '@/lib/backend';
import { Sparkles, Send, Zap } from 'lucide-react';

interface ExploreCard {
  id: string;
  title: string;
  tasks: string;
  work_environment: string;
  outlook: string;
}

function clampStyle(lines: number): React.CSSProperties {
  return {
    display: '-webkit-box',
    WebkitBoxOrient: 'vertical',
    WebkitLineClamp: lines,
    overflow: 'hidden',
  } as React.CSSProperties;
}

export default function Phase2_1Exploration() {
  const [explored, setExplored] = React.useState(false);
  const [isGenerating, setIsGenerating] = React.useState(false);
  const [cards, setCards] = React.useState<ExploreCard[]>([]);
  const [selectedCardId, setSelectedCardId] = React.useState<string | null>(null);
  const [knowledgeText, setKnowledgeText] = React.useState('');
  const [errorMessage, setErrorMessage] = React.useState('');

  const [qaMessages, setQaMessages] = React.useState<Array<{ role: 'user' | 'assistant'; content: string }>>([]);
  const [qaInput, setQaInput] = React.useState('');
  const [isQaLoading, setIsQaLoading] = React.useState(true);
  const [isQaSending, setIsQaSending] = React.useState(false);
  const qaEndRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const artifact = await getLatestArtifact<{ cards?: ExploreCard[] }>('phase2_explore_cards');
        if (!artifact || !mounted) return;
        setCards(artifact.cards || []);
        setExplored(Boolean(artifact.cards?.length));
      } catch {
        // no-op
      }
    };
    load();
    return () => {
      mounted = false;
    };
  }, []);

  React.useEffect(() => {
    let mounted = true;
    const loadQa = async () => {
      setIsQaLoading(true);
      try {
        const history = await getMessagesByStep('phase2', '2-1');
        if (!mounted) return;
        setQaMessages(history);
        const hasAssistant = history.some(msg => msg.role === 'assistant');
        if (!hasAssistant) {
          const res = await runTask('phase2_explore_chat_turn', { user_message: '' });
          if (!mounted) return;
          setQaMessages([{ role: 'assistant', content: String(res.output_json?.assistant_message || '') }]);
        }
      } catch (error) {
        if (!mounted) return;
        setQaMessages([
          {
            role: 'assistant',
            content: getUserErrorMessage(error, 'Q&A를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.'),
          },
        ]);
      } finally {
        if (mounted) setIsQaLoading(false);
      }
    };
    loadQa();
    return () => {
      mounted = false;
    };
  }, []);

  React.useEffect(() => {
    qaEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [qaMessages]);

  const handleExplore = async () => {
    setIsGenerating(true);
    setErrorMessage('');
    try {
      const res = await runTask('phase2_explore', {});
      const nextCards = ((res.output_json as { cards?: ExploreCard[] }).cards || []).map((card, idx) => ({
        ...card,
        id: card.id || `alt-${idx + 1}`,
      }));
      setCards(nextCards);
      setExplored(nextCards.length > 0);
    } catch (error) {
      setErrorMessage(getUserErrorMessage(error, '탐색 결과를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.'));
    } finally {
      setIsGenerating(false);
    }
  };

  const selectedCard = cards.find(card => card.id === selectedCardId) || null;

  const handleSendQa = async () => {
    const content = qaInput.trim();
    if (!content || isQaSending || isQaLoading) return;
    setQaMessages(prev => [...prev, { role: 'user', content }]);
    setQaInput('');
    setIsQaSending(true);
    try {
      const res = await runTask('phase2_explore_chat_turn', {
        user_message: content,
        selected_card: selectedCard
          ? {
              title: selectedCard.title,
              tasks: selectedCard.tasks,
              work_environment: selectedCard.work_environment,
              outlook_salary: selectedCard.outlook,
            }
          : null,
      });
      setQaMessages(prev => [...prev, { role: 'assistant', content: String(res.output_json?.assistant_message || '') }]);
    } catch (error) {
      setQaMessages(prev => [
        ...prev,
        {
          role: 'assistant',
          content: getUserErrorMessage(error, 'Q&A 응답을 가져오지 못했습니다. 잠시 후 다시 시도해 주세요.'),
        },
      ]);
    } finally {
      setIsQaSending(false);
    }
  };

  return (
    <Layout>
      <div className="flex-1 overflow-y-auto p-8" style={{ marginLeft: '260px', marginRight: '360px' }}>
        <div className="max-w-6xl mx-auto">
          <div className="mb-6">
            <span className="text-[13px] mb-1 block" style={{ color: 'var(--color-accent)' }}>
              Phase 2: 직업 탐색
            </span>
            <h1 className="mb-3" style={{ color: 'var(--color-text-primary)' }}>
              직업/대안 정보 탐색
            </h1>
            <div
              className="p-4 rounded-lg"
              style={{ backgroundColor: 'rgba(255,255,255,0.02)', border: '1px solid var(--color-border)' }}
            >
              <p className="text-[14px] mb-1" style={{ color: 'var(--color-text-primary)', fontWeight: 500 }}>
                이번 단계에서 할 일
              </p>
              <p className="text-[14px] leading-relaxed" style={{ color: 'var(--color-text-secondary)' }}>
                사용자의 입력 정보를 바탕으로 생성된 대안 정보를 확인하세요.
                <br />
                아래 '내 대안 지식 정리'에 내가 이해한 내용을 직접 기록합니다.
              </p>
            </div>
          </div>

          {!explored && (
            <div className="flex justify-center py-8">
              <button
                onClick={handleExplore}
                disabled={isGenerating}
                className="flex items-center gap-2.5 px-8 py-4 rounded-lg text-[15px] transition-colors"
                style={{
                  backgroundColor: isGenerating ? 'var(--color-bg-card)' : 'var(--color-accent)',
                  color: 'var(--color-text-primary)',
                  border: isGenerating ? '1px solid var(--color-border)' : 'none',
                }}
              >
                <Sparkles className="w-5 h-5" style={{ strokeWidth: 1.5 }} />
                {isGenerating ? '생성 중...' : '탐색 실행'}
              </button>
            </div>
          )}

          {errorMessage && (
            <div
              className="mb-5 px-4 py-3 rounded-lg text-[13px]"
              style={{
                backgroundColor: 'rgba(239,68,68,0.1)',
                border: '1px solid rgba(239,68,68,0.35)',
                color: 'var(--color-text-primary)',
              }}
            >
              {errorMessage}
            </div>
          )}

          {explored && (
            <div className="grid grid-cols-2 gap-4 mb-6 auto-rows-fr">
              {cards.map(card => {
                const selected = selectedCardId === card.id;
                return (
                  <button
                    key={card.id}
                    type="button"
                    onClick={() => setSelectedCardId(selected ? null : card.id)}
                    className="p-4 rounded-xl text-left h-full flex flex-col"
                    style={{
                      backgroundColor: 'var(--color-bg-card)',
                      border: selected ? '1.5px solid var(--color-accent)' : '1px solid var(--color-border)',
                    }}
                  >
                    <h3
                      className="text-[20px] mb-3"
                      style={{
                        color: 'var(--color-text-primary)',
                        lineHeight: 1.45,
                        minHeight: '5.8rem',
                        ...clampStyle(3),
                      }}
                    >
                      {card.title}
                    </h3>
                    <div className="space-y-3.5 flex-1">
                      <div style={{ minHeight: '10.8rem' }}>
                        <p className="text-[14px] font-semibold mb-1" style={{ color: 'var(--color-text-primary)' }}>
                          하는 일
                        </p>
                        <p
                          className="text-[13px] leading-relaxed"
                          style={{ color: 'var(--color-text-secondary)', lineHeight: 1.72, ...clampStyle(5) }}
                        >
                          {card.tasks}
                        </p>
                      </div>
                      <div style={{ minHeight: '8.7rem' }}>
                        <p className="text-[14px] font-semibold mb-1" style={{ color: 'var(--color-text-primary)' }}>
                          근무 환경
                        </p>
                        <p
                          className="text-[13px] leading-relaxed"
                          style={{ color: 'var(--color-text-secondary)', lineHeight: 1.72, ...clampStyle(4) }}
                        >
                          {card.work_environment}
                        </p>
                      </div>
                      <div style={{ minHeight: '8.7rem' }}>
                        <p className="text-[14px] font-semibold mb-1" style={{ color: 'var(--color-text-primary)' }}>
                          전망
                        </p>
                        <p
                          className="text-[13px] leading-relaxed"
                          style={{ color: 'var(--color-text-secondary)', lineHeight: 1.72, ...clampStyle(4) }}
                        >
                          {card.outlook}
                        </p>
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}

          <div
            className="p-5 rounded-xl mb-6"
            style={{ backgroundColor: 'var(--color-bg-card)', border: '1px solid var(--color-border)' }}
          >
            <div className="flex items-center justify-between mb-2">
              <h3 style={{ color: 'var(--color-text-primary)' }}>내 대안 지식 정리</h3>
              {selectedCard && (
                <span
                  className="text-[12px] px-2.5 py-1 rounded-full"
                  style={{
                    backgroundColor: 'rgba(255,31,86,0.08)',
                    color: 'var(--color-accent)',
                    border: '1px solid rgba(255,31,86,0.15)',
                  }}
                >
                  현재 참고 중: {selectedCard.title}
                </span>
              )}
            </div>
            <textarea
              value={knowledgeText}
              onChange={e => setKnowledgeText(e.target.value)}
              rows={5}
              placeholder="이해한 내용을 자유롭게 정리하세요..."
              className="w-full px-3 py-3 rounded-lg text-[14px] leading-relaxed"
              style={{
                backgroundColor: 'var(--color-bg-surface)',
                border: '1px solid var(--color-border)',
                color: 'var(--color-text-primary)',
                resize: 'vertical',
                outline: 'none',
                lineHeight: 1.72,
              }}
            />
          </div>

          <FooterStepNav
            className="mt-6 flex justify-between"
            onBeforeNext={async () => {
              if (!cards.length) {
                setErrorMessage('먼저 탐색 실행을 완료해 주세요.');
                return false;
              }
              try {
                await runTask('phase2_generate_candidates', {
                  explore: { cards },
                  user_notes: knowledgeText,
                });
                return true;
              } catch (error) {
                setErrorMessage(getUserErrorMessage(error, '다음 단계 생성에 실패했습니다. 잠시 후 다시 시도해 주세요.'));
                return false;
              }
            }}
          />
        </div>
      </div>

      <ContextPanel title="직업/대안 Q&A" icon={Zap}>
        <div className="h-full flex flex-col">
          <div className="px-4 py-2 text-[12px]" style={{ color: 'var(--color-text-secondary)', borderBottom: '1px solid var(--color-border)' }}>
            {selectedCard ? `현재 질문 대상: ${selectedCard.title}` : '카드를 선택하면 해당 대안 중심으로 질문할 수 있습니다.'}
          </div>
          <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
            {isQaLoading && (
              <div
                className="text-[13px]"
                style={{ color: 'var(--color-text-secondary)' }}
              >
                불러오는 중...
              </div>
            )}
            {!isQaLoading && qaMessages.length === 0 && (
              <div className="text-[13px]" style={{ color: 'var(--color-text-secondary)' }}>
                질문을 입력하면 대안 정보를 이어서 탐색할 수 있습니다.
              </div>
            )}
            {qaMessages.map((msg, idx) => (
              <div
                key={idx}
                className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                <div
                  className="px-3 py-2 rounded-xl max-w-[88%] text-[13px] leading-relaxed"
                  style={{
                    backgroundColor: msg.role === 'user' ? 'var(--color-accent)' : 'var(--color-bg-card)',
                    border: msg.role === 'assistant' ? '1px solid var(--color-border)' : 'none',
                    color: 'var(--color-text-primary)',
                    lineHeight: 1.7,
                  }}
                >
                  {msg.content}
                </div>
              </div>
            ))}
            <div ref={qaEndRef} />
          </div>

          <div className="p-4" style={{ borderTop: '1px solid var(--color-border)' }}>
            <div className="flex gap-2">
              <textarea
                value={qaInput}
                onChange={e => setQaInput(e.target.value)}
                rows={2}
                placeholder="질문을 입력하세요..."
                disabled={isQaSending || isQaLoading}
                className="flex-1 px-3 py-2 rounded-lg text-[13px] leading-relaxed"
                style={{
                  backgroundColor: 'var(--color-bg-card)',
                  border: '1px solid var(--color-border)',
                  color: 'var(--color-text-primary)',
                  resize: 'none',
                  outline: 'none',
                  lineHeight: 1.65,
                }}
              />
              <button
                type="button"
                onClick={handleSendQa}
                disabled={isQaSending || isQaLoading || !qaInput.trim()}
                className="px-3 py-2 rounded-lg"
                style={{
                  backgroundColor:
                    isQaSending || isQaLoading || !qaInput.trim()
                      ? 'var(--color-bg-surface)'
                      : 'var(--color-accent)',
                  color: 'var(--color-text-primary)',
                  border:
                    isQaSending || isQaLoading || !qaInput.trim()
                      ? '1px solid var(--color-border)'
                      : 'none',
                }}
              >
                <Send className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>
      </ContextPanel>
    </Layout>
  );
}
