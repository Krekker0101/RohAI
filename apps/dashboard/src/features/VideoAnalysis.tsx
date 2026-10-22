import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, CheckCircle2, Download, Film, Play, Square, Trash2, UploadCloud } from 'lucide-react';
import { downloadJSON, request, requestBlob, uploadVideo } from '../data/api';
import './video-analysis.css';

interface VideoObject {
  track_id: number | null; kind: string; confidence: number; bbox: number[];
  speed_px_per_second: number | null; slow_motion_seconds: number | null;
}
interface Capabilities {
  max_upload_mb: number; max_duration_seconds: number; max_image_pixels?: number;
  vision_dependencies_available: boolean; model_available: boolean;
  aerial_model_available: boolean;
  road_segmentation_available: boolean;
}
interface VideoJob {
  id: string; filename: string; status: string; stage: string; error: string | null;
  media_kind?: 'image' | 'video'; width?: number; height?: number;
  processed_frames: number; total_frames: number | null; progress_percent: number | null;
  media_seconds: number; duration_seconds: number | null; fps: number | null;
  preview_version: number; unique_tracks: number; unique_classes?: Record<string, number>;
  tiles_processed?: number; tiles_per_frame?: number; current_frame?: number;
  road_model?: string; road_method?: string; road_coverage_percent?: number; road_mask_media_seconds?: number;
  stationary_candidates?: number; counts: Record<string, number>; objects: VideoObject[];
  road_geometry?: {status:string; approaches:string[]; lane_count:number|null; intersection_detected:boolean; lane_markings_detected:boolean; confidence:number; reason:string};
  signal_recommendation?: {status:string; physical_execution:boolean; phase:string; reason:string; conflict_check:{passed:boolean;conflicts:string[]}; timer_policy:Record<string,number|string>; signals:Record<string,{vehicles:string;pedestrians:string;stage:string;seconds:number}>};
  estimated_remaining_seconds?: number; report_ready?: boolean;
  explanations: { stage: string; text: string }[];
  timeline: { media_seconds: number; objects: number; counts: Record<string, number>; stationary_candidates: number }[];
}
const completed = new Set(['completed', 'failed', 'cancelled']);
const stages: Record<string, string> = {
  uploading: 'Загрузка файла', validating: 'Проверка видео', loading_model: 'Подготовка нейросети', loading_roads: 'Подготовка распознавания дорог',
  analyzing: 'Анализ кадров', finalizing: 'Подготовка отчёта', completed: 'Анализ завершён',
  failed: 'Анализ остановлен с ошибкой', cancelled: 'Анализ отменён',
};
const kinds: Record<string, string> = {
  car: 'Автомобили', bus: 'Автобусы', truck: 'Грузовики', pedestrian: 'Пешеходы',
  bicycle: 'Велосипеды', motorcycle: 'Мотоциклы',
};
const errors: Record<string, string> = {
  ROAD_MODEL_MISSING: 'Модель дорог не установлена. Выполните установку из инструкции или отключите выделение дорог.',
  ROAD_MODEL_INTEGRITY: 'Модель дорог не прошла проверку целостности. Установите проверенные веса заново.',
  VIDEO_METADATA_INVALID: 'Файл не удалось прочитать как видео. Проверьте формат и целостность.',
  IMAGE_DECODE_FAILED: 'Не удалось прочитать изображение. Проверьте формат и целостность файла.',
  IMAGE_RESOLUTION_LIMIT: 'Изображение превышает лимит 3840 × 2160.',
  VIDEO_DECODE_FAILED: 'Видеофайл повреждён: декодирование оборвалось до конца.',
  VIDEO_DURATION_LIMIT: 'Видео превышает допустимую продолжительность или количество кадров.',
  VIDEO_RESOLUTION_LIMIT: 'Поддерживается видео до 3840 × 2160.',
  MODEL_MISSING: 'Модель не установлена на backend. Подготовьте Vision Engine перед анализом.',
  ANALYSIS_FAILED: 'Не удалось выполнить анализ. Подробности сохранены в журнале задачи.',
  WORKER_TIMEOUT: 'Обработка превысила время ожидания. Задача остановлена.',
  SERVER_RESTARTED: 'Backend перезапустился во время обработки. Загрузите видео повторно.',
  WORKER_EXITED: 'Процесс анализа неожиданно завершился.',
};
const duration = (seconds: number | null | undefined) => seconds === null || seconds === undefined
  ? '—' : `${Math.floor(seconds / 60)}:${Math.floor(seconds % 60).toString().padStart(2, '0')}`;

export default function VideoAnalysis() {
  const [jobs, setJobs] = useState<VideoJob[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [job, setJob] = useState<VideoJob | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [profile, setProfile] = useState<'standard' | 'aerial' | 'aerial_precise'>('aerial');
  const [roads, setRoads] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [uploadPercent, setUploadPercent] = useState(0);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState('');
  const [exporting, setExporting] = useState(false);
  const [presentation, setPresentation] = useState(false);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const uploadController = useRef<AbortController | null>(null);
  const previewUrl = useRef('');
  const mounted = useRef(true);

  const reload = useCallback(async (signal?: AbortSignal) => {
    const rows = await request<VideoJob[]>('/api/v1/video/jobs', 'GET', undefined, signal);
    if (signal?.aborted || !mounted.current) return;
    setJobs(rows);
    setSelected(value => value ?? rows[0]?.id ?? null);
  }, []);
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController();
    void request<Capabilities>('/api/v1/video/capabilities', 'GET', undefined,
      AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]))
      .then(data => { if (!controller.signal.aborted) setCapabilities(data); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(String(reason)); });
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refreshList = async () => {
      try { await reload(AbortSignal.any([controller.signal, AbortSignal.timeout(10000)])); }
      catch (reason) { if (!controller.signal.aborted) setError(String(reason)); }
      if (!controller.signal.aborted) timer = setTimeout(() => { void refreshList(); }, 5000);
    };
    void refreshList();
    return () => { mounted.current = false; controller.abort(); if (timer) clearTimeout(timer); uploadController.current?.abort(); };
  }, [reload]);
  useEffect(() => {
    if (!selected) { setJob(null); return; }
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    setJob(null);
    setError('');
    const poll = async () => {
      try {
        const data = await request<VideoJob>(`/api/v1/video/jobs/${selected}`, 'GET', undefined,
          AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]));
        if (controller.signal.aborted) return;
        setJob(data);
        setError('');
        if (!completed.has(data.status)) timer = setTimeout(() => { void poll(); }, 750);
        else void reload(controller.signal).catch(() => undefined);
      } catch (reason) {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : 'Не удалось получить прогресс');
          timer = setTimeout(() => { void poll(); }, 2000);
        }
      }
    };
    void poll();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }, [selected, reload]);
  useEffect(() => {
    setPreview('');
    if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
    previewUrl.current = '';
    return () => {
      if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
      previewUrl.current = '';
    };
  }, [selected]);
  useEffect(() => {
    if (!job?.preview_version) return;
    const controller = new AbortController();
    void requestBlob(`/api/v1/video/jobs/${job.id}/preview?v=${job.preview_version}`,
      AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]))
      .then(blob => {
        if (controller.signal.aborted) return;
        const url = URL.createObjectURL(blob);
        if (previewUrl.current) URL.revokeObjectURL(previewUrl.current);
        previewUrl.current = url;
        setPreview(url);
      }).catch(() => undefined);
    return () => { controller.abort(); };
  }, [job?.id, job?.preview_version]);

  async function start() {
    if (!file) return;
    if (capabilities && file.size > capabilities.max_upload_mb * 1024 * 1024) {
      setError(`Размер файла превышает ${capabilities.max_upload_mb} МБ`); return;
    }
    const controller = new AbortController();
    uploadController.current = controller;
    setUploading(true); setUploadPercent(0); setError('');
    try {
      const data = await uploadVideo<VideoJob>(file, setUploadPercent, controller.signal, profile, roads);
      if (!mounted.current) return;
      setJobs(rows => [data, ...rows.filter(row => row.id !== data.id)]);
      setSelected(data.id);
    } catch (reason) {
      if (mounted.current) {
        setError(reason instanceof DOMException && reason.name === 'AbortError'
          ? 'Загрузка отменена' : reason instanceof Error ? reason.message : 'Ошибка загрузки');
        void reload().catch(() => undefined);
      }
    } finally { if (mounted.current) setUploading(false); uploadController.current = null; }
  }
  async function cancel() {
    if (!job) return;
    try { setJob(await request<VideoJob>(`/api/v1/video/jobs/${job.id}/cancel`, 'POST')); }
    catch (reason) { setError(String(reason)); }
  }
  async function remove() {
    if (!job) return;
    try {
      await request(`/api/v1/video/jobs/${job.id}`, 'DELETE');
      setSelected(null); setJob(null); await reload();
    } catch (reason) { setError(String(reason)); }
  }
  async function exportReport() {
    if (!job) return;
    setExporting(true);
    try { downloadJSON(await request(`/api/v1/video/jobs/${job.id}/report`), 'rohai-video-report.json'); }
    catch (reason) { setError(String(reason)); }
    finally { if (mounted.current) setExporting(false); }
  }
  const active = job !== null && !completed.has(job.status);
  const showActivity = active && !error;
  const busy = uploading || jobs.some(row => !completed.has(row.status)) || active;
  const progress = job?.progress_percent ?? 0;
  const available = capabilities?.vision_dependencies_available
    && (profile !== 'standard' ? capabilities?.aerial_model_available : capabilities?.model_available);
  const ready = available && (!roads || capabilities?.road_segmentation_available);
  const stageIndex = job?.stage === 'loading_model' || job?.stage === 'loading_roads' ? 1 : job?.stage === 'analyzing' ? 2
    : job?.stage === 'finalizing' ? 3 : job?.stage === 'completed' ? 4 : 0;
  return <div className={`video-workspace ${presentation ? 'video-presentation' : ''}`}>
    <section className="card video-import">
      <label className="road-toggle"><input type="checkbox" checked={roads} disabled={uploading} onChange={event => setRoads(event.target.checked)}/>Подсвечивать дороги <span>Нейросеть; для дрона — экспериментальное уточнение поверхности. Может ошибаться.</span></label>
      <div className="video-upload-row"><label>Режим распознавания <select aria-label="Режим распознавания" value={profile} disabled={uploading} onChange={event => setProfile(event.target.value as 'standard' | 'aerial' | 'aerial_precise')}><option value="aerial">Дрон / дальний транспорт</option><option value="aerial_precise">Дрон / дополнительно проверить повороты</option><option value="standard">Обычная камера / люди и транспорт</option></select></label><p className="table-note">Дрон: YOLO26s, увеличенные участки кадра, единые треки и фильтр крупных рамок. Дополнительные повороты помогают при разной ориентации машин, но заметно замедляют обработку. На CPU анализ может занимать десятки минут или часы.</p></div>
      <div className="card-head"><div><h2>Анализ фото и видео</h2><p>Загрузите фото для снимка или видео для анализа всех кадров и траекторий.</p></div><Film size={24}/></div>
      <div className="video-upload-row"><label className="video-file-picker"><UploadCloud size={30}/><span>{file ? file.name : 'Выбрать фото или видео'}</span><small>JPG, PNG, WEBP · MP4, MOV, AVI, MKV, WEBM · до {capabilities?.max_upload_mb ?? '…'} МБ</small><input type="file" aria-label="Выбрать фото или видео для анализа" accept=".jpg,.jpeg,.png,.bmp,.webp,.mp4,.mov,.avi,.mkv,.webm,.m4v" disabled={uploading} onChange={event => setFile(event.target.files?.[0] ?? null)}/></label>
        <button className="primary-button" disabled={!file || busy || !ready} onClick={() => { void start(); }}><Play size={17}/>Загрузить и анализировать</button>
        {uploading ? <button className="secondary-button" onClick={() => uploadController.current?.abort()}><Square size={16}/>Отменить загрузку</button> : null}
      </div>
      {uploading ? <div className="video-transfer" role="status"><span>Передача файла: {uploadPercent}%{uploadPercent === 100 ? ' · подготовка задачи…' : ''}</span><progress max={100} value={uploadPercent}/></div> : null}
      {error ? <p className="video-error" role="alert">{error}</p> : null}
      {capabilities && !available ? <p className="video-error" role="alert">Vision Engine не готов: проверьте установку модели и библиотек анализа на backend.</p> : null}
      {capabilities && roads && !capabilities.road_segmentation_available ? <p className="video-error" role="alert">Модель дорог не готова. Установите её по инструкции или отключите «Подсвечивать дороги».</p> : null}
      {!capabilities || !available ? <div className="video-actions"><button className="secondary-button" onClick={() => {
        void request<Capabilities>('/api/v1/video/capabilities').then(data => { setCapabilities(data); setError(''); }).catch(reason => setError(String(reason)));
      }}>Проверить готовность</button></div> : null}
    </section>
    {jobs.length ? <div className="video-history"><label>Запись <select aria-label="История видеоанализа" value={selected ?? ''} onChange={event => setSelected(event.target.value)}>{jobs.map(row => <option key={row.id} value={row.id}>{row.filename} · {stages[row.stage] ?? row.status}</option>)}</select></label><button className="secondary-button" onClick={() => setPresentation(value => !value)}>{presentation ? 'Обычный вид' : 'Режим презентации'}</button></div> : null}
    {job ? <>
      <section className="card video-progress-card"><div className="card-head"><div><h2>{stages[job.stage] ?? job.stage}</h2><p>{job.filename}</p></div><strong className="video-percent">{job.progress_percent === null ? '…' : `${progress.toFixed(1)}%`}</strong></div>
        {showActivity ? <div className="analysis-motion" role="status"><span className="analysis-orbit" aria-hidden="true"><i/><i/><i/></span><div><strong>{stages[job.stage] ?? 'Анализ выполняется'}</strong><p>Обработано {job.processed_frames} кадров{job.tiles_per_frame ? ` · участков текущего кадра: ${job.tiles_processed ?? 0}/${job.tiles_per_frame}` : ''}. Показатели обновляются по фактической работе.</p></div><span className="analysis-wave" aria-hidden="true">{[0,1,2,3,4,5,6].map(index => <i key={index} style={{ animationDelay: `${index * 110}ms` }}/>)}</span></div> : null}
        <progress aria-label="Прогресс анализа видео" max={100} value={progress}/>
        {active && job.tiles_per_frame ? <p className="table-note" role="status">Кадр {job.current_frame ?? job.processed_frames}: обработано участков {job.tiles_processed ?? 0} / {job.tiles_per_frame}</p> : null}
        <ol className="video-stages">{['Проверка файла', 'Нейросеть', 'Кадры и треки', 'Отчёт'].map((label, index) => <li key={label} className={stageIndex > index ? 'done' : stageIndex === index ? 'current' : ''}>{stageIndex > index ? <CheckCircle2 size={16}/> : <span>{index + 1}</span>}{label}</li>)}</ol>
        <div className="video-progress-meta"><span>{job.media_kind === 'image' ? 'Изображение' : 'Кадры'} <b>{job.processed_frames} / {job.total_frames ?? '—'}</b></span>{job.media_kind !== 'image' ? <><span>Запись <b>{duration(job.media_seconds)} / {duration(job.duration_seconds)}</b></span><span>Обработка <b>{job.fps?.toFixed(1) ?? '—'} FPS</b></span>{active ? <span>Осталось примерно <b>{duration(job.estimated_remaining_seconds)}</b></span> : null}</> : null}</div>
        {job.error ? <p className="video-error" role="alert">{errors[job.error] ?? job.error}</p> : null}
        <div className="video-actions">{active ? <button className="secondary-button" onClick={() => { void cancel(); }}><Square size={16}/>Остановить анализ</button> : <button className="secondary-button" onClick={() => { void remove(); }}><Trash2 size={16}/>Удалить запись и результаты</button>}
        <button className="secondary-button" disabled={job.status !== 'completed' || exporting} onClick={() => { void exportReport(); }}><Download size={16}/>{exporting ? 'Загружаем отчёт…' : 'Скачать отчёт'}</button></div>
      </section>
      <div className="video-analysis-grid"><section className="card video-view"><div className="card-head"><h2>Что видит система</h2><span className="badge green">{duration(job.media_seconds)}</span></div>
        {showActivity ? <div className="analysis-scan" aria-hidden="true"><i/><span>VISION ENGINE · {stages[job.stage] ?? 'АНАЛИЗ'}</span></div> : null}
        {job.road_model ? <p className="table-note road-legend"><i/>{job.road_method?.includes('heuristic') ? 'Кандидат дороги: нейросеть + уточнение поверхности' : 'Дорога: нейросетевая маска'} · {job.road_coverage_percent?.toFixed(1)}% кадра · маска на {duration(job.road_mask_media_seconds)}</p> : null}
        <div className="video-preview">{preview ? <img src={preview} alt={`Проанализированный кадр ${job.preview_version} с объектами и траекториями`}/> : <div className="video-preview-empty"><Film size={48}/><p>{active ? 'Первый размеченный кадр появится после подготовки модели' : 'Размеченный кадр недоступен'}</p></div>}</div>
        <p className="table-note">Рамки — найденные объекты; номер — ID трека; линии — история движения. Анализ проходит все кадры последовательно, независимо от скорости воспроизведения.</p>
        {job.road_geometry || job.signal_recommendation ? <div className="video-traffic-results"><h3>Дороги и рекомендации сигналов</h3>{job.road_geometry ? <p><b>Геометрия:</b> {job.road_geometry.intersection_detected ? 'обнаружены признаки перекрёстка' : 'недостаточно данных для подтверждения перекрёстка'} · подходы: {job.road_geometry.approaches.join(', ') || 'не определены'} · полосы: {job.road_geometry.lane_count ?? 'не определены'}<br/>{job.road_geometry.reason}</p> : null}{job.signal_recommendation ? <><p><b>Рекомендация:</b> {job.signal_recommendation.reason} Таймер: зелёный {job.signal_recommendation.timer_policy.green_seconds} с, жёлтый {job.signal_recommendation.timer_policy.yellow_seconds} с, общий красный {job.signal_recommendation.timer_policy.all_red_seconds} с.</p><div className="video-signal-grid">{Object.entries(job.signal_recommendation.signals).map(([direction,signal])=><div key={direction} className={`video-signal ${signal.vehicles}`}><b>{({north:'Север',south:'Юг',east:'Восток',west:'Запад'} as Record<string,string>)[direction] ?? direction}</b><span>{signal.vehicles === 'green' ? '🟢 Зелёный' : signal.vehicles === 'yellow' ? '🟡 Жёлтый' : '🔴 Красный'}</span><small>{signal.seconds} с · {signal.stage}</small></div>)}</div><p className="table-note">Это офлайн-рекомендация; изображение и запись не переключают аппаратные светофоры. Проверка конфликтов: {job.signal_recommendation.conflict_check.passed ? 'пройдена' : 'не пройдена'}.</p></> : null}</div> : null}
      </section><section className="card video-evidence"><div className="card-head"><h2>Ход анализа</h2><Activity size={20}/></div>
        <div className="video-evidence-list"><div><span>01 · Декодирование</span><strong>{job.processed_frames} кадров прочитано</strong></div><div><span>02 · Обнаружение</span><strong>{Object.values(job.counts).reduce((sum, count) => sum + count, 0)} объектов в текущем кадре</strong><p>{Object.entries(job.counts).map(([kind, count]) => `${kinds[kind] ?? kind}: ${count}`).join(' · ') || 'Объекты пока не обнаружены'}</p></div><div><span>03 · Сопоставление</span><strong>{job.unique_tracks} уникальных треков за запись</strong><p>ID сохраняется между соседними кадрами, пока трекер сопоставляет объект.</p></div><div><span>04 · Движение</span><strong>{job.stationary_candidates ?? 0} треков с малым движением</strong><p>Низкое движение в изображении не подтверждает дорожную очередь. Для её измерения нужна калибровка.</p></div></div>
      </section></div>
      <section className="card"><div className="card-head"><h2>Наблюдения по ходу записи</h2><span className="badge blue">Время видео</span></div><div className="video-timeline" aria-label="Количество объектов по времени записи">{job.timeline.map(point => <div key={point.media_seconds} title={`${duration(point.media_seconds)}: ${point.objects} объектов`}><i style={{ height: `${point.objects / Math.max(1, ...job.timeline.map(item => item.objects)) * 100}%` }}/><span>{duration(point.media_seconds)}</span></div>)}</div>{!job.timeline.length ? <p className="empty-small">Появится после обработки первых кадров.</p> : <p className="table-note">Показаны последние 120 точек; полный временной ряд доступен в итоговом отчёте.</p>}</section>
      <section className="card"><div className="card-head"><h2>Объекты текущего кадра</h2></div><div className="table-scroll"><table><thead><tr><th>ID</th><th>Класс</th><th>Уверенность модели</th><th>Движение, px/s</th><th>Малое движение, с</th></tr></thead><tbody>{job.objects.slice(0, 50).map((object, index) => <tr key={`${object.track_id}:${index}`}><td>{object.track_id ?? 'Не сопоставлен'}</td><td>{kinds[object.kind] ?? object.kind}</td><td>{(object.confidence * 100).toFixed(1)}%</td><td>{object.speed_px_per_second?.toFixed(1) ?? '—'}</td><td>{object.slow_motion_seconds?.toFixed(1) ?? '—'}</td></tr>)}</tbody></table></div><p className="table-note">Уверенность модели не равна проверенной точности. Количество ID не является измеренным транспортным потоком. Показаны до 50 объектов.</p></section>
    </> : <section className="card video-welcome"><Film size={56}/><h2>Запись превращается в наблюдения</h2><p>Камера и запуск в реальном времени не нужны. Выберите видео: RohAI покажет обработанные кадры, обнаруженные классы и траектории, затем подготовит отчёт.</p></section>}
  </div>;
}
