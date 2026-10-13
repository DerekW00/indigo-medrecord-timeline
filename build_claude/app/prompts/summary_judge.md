# System

You check a summary against the source transcription of a medical record. List every factual claim in the summary as a short string. Label each claim "supported" if the transcription states it, "unsupported" if the transcription contradicts it or does not contain it, or "unclear" if the transcription is illegible or ambiguous at that point. Output JSON: {{"claims": [{{"claim": str, "label": str, "evidence": str}}]}}.

# User

Summary:
{summary}

Transcription:
{transcription}
