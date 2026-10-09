// Fail closed: editor drafts and template responses never become live calibration.
export function overlayState(state, width, height, editing = false, presentation = false) {
  if (editing && !presentation) return 'CALIBRATING';
  const saved = state?.overlay_calibration;
  if (!saved || saved.state === 'NO_CALIBRATION') return 'NO_CALIBRATION';
  const g = saved.geometry;
  return saved.state === 'CALIBRATED' && g &&
    g.source_id === saved.source_id && g.reference_width === width &&
    g.reference_height === height && state.preflight?.calibration_valid === true &&
    state.preflight?.calibration_complete === true && state.calibration_status === 'VALID' &&
    !state.camera_revalidation_required && state.camera_connection === 'CONNECTED'
    ? 'CALIBRATED' : 'CALIBRATION_INVALID';
}
export function liveGeometry(state, width, height) {
  return overlayState(state, width, height) === 'CALIBRATED' ? state.overlay_calibration.geometry : null;
}
export function realTracks(state) {
  return state?.camera_connection === 'CONNECTED' ? (state.tracks ?? []).filter(t =>
    t.bbox && ['x1','y1','x2','y2'].every(k => Number.isFinite(t.bbox[k])) &&
    t.bbox.x2 > t.bbox.x1 && t.bbox.y2 > t.bbox.y1) : [];
}

export function frameTracks(state, frame) {
  const v = state?.vision_state;
  if (!v?.fresh || !frame || v.frame_id !== frame.id || v.stream_epoch !== frame.epoch ||
      v.frame_width !== frame.width || v.frame_height !== frame.height) return [];
  return realTracks(state).filter(t => t.visible !== false);
}
