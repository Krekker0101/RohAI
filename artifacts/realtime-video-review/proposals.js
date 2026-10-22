const $=id=>document.getElementById(id),data=JSON.parse($('data').textContent);
const viewport=$('viewport'),stage=$('stage'),image=$('frame'),overlay=$('overlay');
let index=0;
const detections=()=>data.frames[index].detections.filter(item=>item.confidence>=Number($('threshold').value));
function resize(){
 const frame=data.frames[index],zoom=Number($('zoom').value);
 stage.style.width=`${viewport.clientWidth*zoom}px`;
 stage.style.aspectRatio=`${frame.width}/${frame.height}`;
 $('zoom-value').value=`${zoom}×`;
}
function render(resetView=false){
 const frame=data.frames[index],items=detections(),threshold=Number($('threshold').value);
 image.src=frame.image;image.alt=`Кадр ${frame.index}, предложения модели`;
 overlay.setAttribute('viewBox',`0 0 ${frame.width} ${frame.height}`);
 overlay.replaceChildren();
 for(const item of items){
  const [x1,y1,x2,y2]=item.bbox,g=document.createElementNS('http://www.w3.org/2000/svg','g');
  const rect=document.createElementNS('http://www.w3.org/2000/svg','rect');
  Object.entries({x:x1,y:y1,width:x2-x1,height:y2-y1}).forEach(([key,value])=>rect.setAttribute(key,String(value)));
  const label=document.createElementNS('http://www.w3.org/2000/svg','text');
  label.setAttribute('x',String(x1));label.setAttribute('y',String(Math.max(48,y1-8)));
  label.textContent=`${item.class} ${Math.round(item.confidence*100)}%`;
  g.append(rect,label);overlay.append(g);
 }
 $('position').textContent=`${index+1} / ${data.frames.length} · кадр ${frame.index} · ${frame.time.toFixed(2)} с`;
 $('frame-info').textContent=`Показано ${items.length} из ${frame.detections.length} предложений при пороге ${threshold.toFixed(2)}. Изменение порога скрывает рамки, исходный результат модели не меняется.`;
 $('threshold-value').value=threshold.toFixed(2);
 $('detections').replaceChildren(...items.map(item=>{
  const row=document.createElement('tr');
  for(const value of [item.class,`${(item.confidence*100).toFixed(1)}%`,item.bbox.map(n=>n.toFixed(1)).join(', ')]){
   const cell=document.createElement('td');cell.textContent=value;row.append(cell);
  }
  return row;
 }));
 $('previous').disabled=index===0;$('next').disabled=index===data.frames.length-1;
 resize();if(resetView)viewport.scrollTo(0,0);
}
$('previous').onclick=()=>{if(index>0){index--;render(true);}};
$('next').onclick=()=>{if(index<data.frames.length-1){index++;render(true);}};
$('zoom').oninput=resize;
$('threshold').oninput=()=>render();
window.addEventListener('resize',resize);
window.addEventListener('keydown',event=>{
 if(event.target instanceof HTMLElement&&['INPUT','BUTTON','SELECT'].includes(event.target.tagName))return;
 if(event.key==='ArrowLeft'&&index>0){index--;render(true);}
 if(event.key==='ArrowRight'&&index<data.frames.length-1){index++;render(true);}
});
const hash=data.model_sha256??'не указан';
$('model').textContent=`${data.model} · порог модели ${data.confidence} · SHA256 ${hash}`;
$('threshold').value=String(Math.min(.8,Math.max(.25,data.confidence)));
image.onerror=()=>{$('frame-info').textContent='Исходный кадр не найден. Откройте страницу рядом с папкой frames.';};
render(true);
