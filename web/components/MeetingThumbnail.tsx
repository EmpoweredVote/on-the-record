import { buildThumbnailModel } from "@/lib/thumbnail";
import type { Meeting } from "@/lib/types";

export default function MeetingThumbnail({ meeting }: { meeting: Meeting }) {
  const t = buildThumbnailModel(meeting);

  return (
    <div className="meetingThumb">
      {t.imageSrc ? (
        // Static export has no image optimizer; an intentional lazy <img> is correct here.
        // eslint-disable-next-line @next/next/no-img-element
        <img className="meetingThumbImg" src={t.imageSrc} alt="" loading="lazy" />
      ) : (
        <div className="meetingThumbBand">
          <span className="meetingThumbLoc">{t.location}</span>
          <span className="meetingThumbDate">{t.date}</span>
        </div>
      )}
      {t.showPlay && <span className="meetingThumbPlay" aria-hidden="true" />}
      {t.duration && <span className="meetingThumbDuration">{t.duration}</span>}
      {t.transcriptOnly && (
        <span className="meetingThumbTranscript">
          <svg
            width="11"
            height="11"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            aria-hidden="true"
          >
            <path d="M3 3l18 18M10 7h7a2 2 0 012 2v6m-2 2H6a2 2 0 01-2-2V8" />
          </svg>
          Transcript only
        </span>
      )}
    </div>
  );
}
