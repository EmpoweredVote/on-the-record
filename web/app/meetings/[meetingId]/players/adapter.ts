// Common control surface every player exposes to MeetingView. Sync, deep
// links, and click-to-seek are written against this interface only, so adding
// a provider (Vimeo, self-hosted bucket, ...) means one new component here
// and zero changes to the transcript logic.
export interface PlayerAdapter {
  seekTo(seconds: number): void;
  getCurrentTime(): number;
  isPlaying(): boolean;
}
