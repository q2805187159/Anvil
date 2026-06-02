import type { StepTranscriptMessage } from "@/src/core/threads/message-reducer";

export type TranscriptTurn = {
  user: StepTranscriptMessage | null;
  assistantMessages: StepTranscriptMessage[];
};

export function buildTranscriptTurns(messages: StepTranscriptMessage[]): TranscriptTurn[] {
  const turns: TranscriptTurn[] = [];
  for (const message of messages) {
    if (message.role === "human" || message.role === "user") {
      turns.push({ user: message, assistantMessages: [] });
      continue;
    }
    const currentTurn = turns[turns.length - 1];
    if (!currentTurn || shouldStartAssistantOnlyTurn(currentTurn, message)) {
      turns.push({ user: null, assistantMessages: [message] });
      continue;
    }
    currentTurn.assistantMessages.push(message);
  }
  return turns;
}

function shouldStartAssistantOnlyTurn(turn: TranscriptTurn, message: StepTranscriptMessage) {
  if (!message.live || turn.assistantMessages.length === 0) {
    return false;
  }
  return turn.assistantMessages.some((item) => !item.live);
}
