-- civic.meetings.playback_kind gains an 'audio' value for podcast / radio
-- episodes (a direct MP3/M4A enclosure). The column is plain text with no CHECK
-- constraint, so this migration only updates the documentation comment; no data
-- or schema change is required to start writing 'audio'.
COMMENT ON COLUMN civic.meetings.playback_kind IS
  '''youtube'' | ''file'' | ''hls'' | ''audio'' | null (extensible: ''vimeo'', ''self_hosted''...)';
