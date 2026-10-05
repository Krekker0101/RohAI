# Maket calibration

Wizard работает на raw live frame, canvas coordinates normalized [0,1]. Field
detection_zone — intersection boundary; для каждого N/S/E/W задаются incoming,
queue_zone, lanes, entry_line, stop_line, exit_line, waiting_zone, crosswalk_zone,
exit_zone. Polygon минимум три distinct vertices, без self-intersection; line
задаётся start/end и третьим upstream anchor вне line. Lane polygons keyed IDs.
Каждая approach должна охватывать hand-moved car footprint до stop line.

Файл calibration.json содержит intersection ID, camera source ID, resolution,
created_at/version, orientation, scale, geometry и SHA256 live reference image.
Reference сохраняется backend с текущего frame; stale uploaded image не принимается.
Изменение сохраняется только при stopped control; backend автоматически перезапускает
analyzer. Preflight требует все entry/exit lines, lanes и pedestrian zones для каждого
направления. Control остаётся stopped/SHADOW; demo draft недостаточен.
Orientation — metadata surveyed axes, не автоматический поворот image.

Top-down model_width_cm/model_height_cm задают явные model cm. Без scale analytics
сохраняет normalized positions, px/s и px stop distances; никогда не выдаёт m.
Optional normalized_to_model_homography — nonsingular 3×3 matrix, output cm.
Её коэффициенты и dimensions должны быть измерены и проверены независимо; wizard
не заявляет измеренную metric accuracy по одному вводу matrix. road-equivalent ratio
только metadata и не преобразует physical toy dynamics в road state.
Road-meter CameraCalibration в Maket configuration запрещён.

Resolution/source ID mismatch даёт INVALID. ORB/RANSAC register static reference
вне vehicle approaches: shift выше configured max_alignment_shift_px даёт SUSPECT.
Недостаточно static features — SUSPECT, а не автоматическое предположение unchanged.
Alignment evidence expires через 1.5 s; worker stall не оставляет вечный VALID.
Следует добавить контрастные неподвижные fiducials и проверить moved-camera cases.
ORB sensitivity/specificity на настоящем макете здесь не измерены; похожая сцена
или occluded landmarks остаются рисками. Reconnect требует renewed preflight и ARM.

Расположение линий влияет на event counts. Исчезновение track не считается departure;
only observed oriented crossing. Alias reconciliation TrackStateEstimator сохраняет
identity при допустимом кратком occlusion; реальные ID switches требуют labelled GT.
