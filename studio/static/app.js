'use strict';
const $=s=>document.querySelector(s);
const escapeHTML=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const busy=p=>['drafting','illustrating','rendering'].includes(p?.status);
let config={},project=null,step='story',poll=null,dirty=false,requestPending=false;
async function api(path,options={}){
 const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
 const body=await response.json();
 if(!response.ok)throw Error(typeof body.detail==='string'?body.detail:'Please check the fields and try again.');
 return body;
}
function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').hidden=true,4500);}
async function action(fn){if(requestPending)return;requestPending=true;try{await fn();}catch(e){toast(e.message);}finally{requestPending=false;}}
function fileURL(name){return `/api/projects/${project.id}/files/${encodeURIComponent(name)}`;}
async function refreshProjects(){const projects=await api('/api/projects');$('#projects').innerHTML=projects.map(p=>`<button data-project="${p.id}" class="${project?.id===p.id?'selected':''}">${escapeHTML(p.title)}<small>${escapeHTML(p.status==='ready'?'Ready to watch':p.status)}</small></button>`).join('');}
function setStep(next){step=next;document.querySelectorAll('[data-step]').forEach(el=>el.classList.toggle('active',el.dataset.step===step));for(const key of ['story','board','reel'])$(`#${key}-panel`).hidden=key!==step;sessionStorage.setItem('studio-step',step);}
function storyEditor(){
 const story=project.story;$('#story-title').value=story.title;$('#logline').value=story.logline;$('#character-notes').value=story.character_notes;
 $('#scene-editor').innerHTML=story.scenes.map((s,i)=>`<article class="scene-edit" data-scene="${i}"><div class="scene-top"><span>SCENE ${String(i+1).padStart(2,'0')}</span><button data-remove="${i}" ${story.scenes.length<=3?'disabled':''}>Remove</button></div><label>Narration<textarea data-field="narration" rows="4" maxlength="700">${escapeHTML(s.narration)}</textarea></label><label>What we see<textarea data-field="visual" rows="4" maxlength="2200">${escapeHTML(s.visual)}</textarea></label><label class="motion-label">Character movement<select data-field="movement"><option value="breathe" ${s.movement==='breathe'?'selected':''}>Subtle breathing</option><option value="nod" ${s.movement==='nod'?'selected':''}>A gentle nod</option><option value="still" ${s.movement==='still'?'selected':''}>Hold the pose</option></select></label></article>`).join('');
 updateWordCount();
}
function collectStory(){return {title:$('#story-title').value.trim(),logline:$('#logline').value.trim(),character_notes:$('#character-notes').value.trim(),scenes:[...document.querySelectorAll('.scene-edit')].map(card=>Object.fromEntries(['narration','visual','movement'].map(key=>[key,card.querySelector(`[data-field="${key}"]`).value.trim()])))};}
function updateWordCount(){const text=[...document.querySelectorAll('[data-field="narration"]')].map(el=>el.value).join(' ').trim();$('#word-count').textContent=`${text?text.split(/\s+/).length:0} spoken words`;}
function drawBoard(){
 $('#storyboard').innerHTML=project.story.scenes.map((s,i)=>{const image=project.assets[String(i)];return `<article class="board-card"><div class="board-image">${image?`<img src="${fileURL(image.file)}" alt="Scene ${i+1} illustration">`:'Awaiting illustration'}<b>SCENE ${i+1}</b></div><div class="board-copy"><p>${escapeHTML(s.narration)}</p><details><summary>Visual direction</summary><p>${escapeHTML(s.visual)}</p></details><label class="upload-label">${image?'Replace illustration':'Upload illustration'}<input type="file" accept="image/png,image/jpeg,image/webp" data-upload="${i}" data-kind="image" ${busy(project)||!project.story_approval?'disabled':''}></label><label class="upload-label">${project.audio[String(i)]?'Voice recording added ✓':'Use your own voice (optional)'}<input type="file" accept="audio/*" data-upload="${i}" data-kind="audio" ${busy(project)||!project.story_approval?'disabled':''}></label></div></article>`;}).join('');
 const missing=project.story.scenes.length-Object.keys(project.assets).length;
 $('#generate-images').textContent=missing?`Generate ${missing} illustration${missing===1?'':'s'} ↗`:'All illustrations added ✓';
 $('#generate-images').disabled=busy(project)||!project.story_approval||!config.ai_available||!missing;
 $('#image-cost').textContent=!project.story_approval?'Approve your story first.':missing?`Generating creates ${missing} new image${missing===1?'':'s'} and may incur AI provider charges. Existing images are reused. You can upload your own instead.`:'Review all the illustrations. You can replace any scene with an upload.';
 $('#approve-board').disabled=busy(project)||!project.story_approval||missing>0;
}
function showProject(p,nextStep){
 project=p;dirty=false;clearTimeout(poll);$('#welcome').hidden=true;$('#workspace').hidden=false;
 $('#project-title').textContent=p.story?.title||'Writing your story…';$('#reel-title').textContent=p.story?.title||'';
 $('#job-status').textContent=p.progress;$('#job-status').classList.toggle('busy',busy(p));$('#error').hidden=!p.error;$('#error').textContent=p.error||'';
 $('#download-story').href=fileURL('story.json');$('#download-story').hidden=!p.story;
 if(p.story){
  storyEditor();drawBoard();
  $('#story-approval-note').textContent=p.story_approval?'Story approved. Editing it resets the storyboard and approvals.':'Nothing is illustrated until you approve this story.';
  $('#save-story').disabled=busy(p);$('#approve-story').disabled=busy(p);$('#add-scene').disabled=busy(p)||p.story.scenes.length>=8;
  document.querySelectorAll('#story-panel input,#story-panel textarea,#story-panel select,#story-panel [data-remove]').forEach(el=>el.disabled=busy(p)||(el.hasAttribute('data-remove')&&p.story.scenes.length<=3));
 }
 document.querySelector('[data-step="story"]').classList.toggle('done',!!p.story_approval);document.querySelector('[data-step="board"]').classList.toggle('done',!!p.storyboard_approval);document.querySelector('[data-step="reel"]').classList.toggle('done',!!p.video);
 $('#render-button').disabled=busy(p)||!p.storyboard_approval||!config.ffmpeg;$('#render-button').hidden=!!p.video;
 $('#download-video').hidden=!p.video;$('#download-captions').hidden=!p.captions;
 if(p.video){$('#video-container').innerHTML=`<video controls playsinline preload="metadata" src="${fileURL(p.video)}"></video>`;$('#download-video').href=fileURL(p.video);$('#download-captions').href=fileURL(p.captions);$('#reel-detail').textContent=`${Math.round(p.duration||0)} seconds · 720 × 1280 · MP4`;}
 else{$('#video-container').innerHTML='<div class="video-placeholder">Your reel will appear here.<small>Approve your story and storyboard first.</small></div>';$('#reel-detail').textContent='Your narration sets the pace. We leave space for the expressions and ending.';}
 setStep(nextStep||step);if(!p.story)$('#story-panel').hidden=true;
 sessionStorage.setItem('studio-project',p.id);refreshProjects().catch(e=>toast(e.message));
 if(busy(p))poll=setTimeout(async()=>{try{showProject(await api(`/api/projects/${p.id}`));}catch(e){toast(e.message);}},1800);
}
async function saveDraft(){if(!project?.story)return;const p=await api(`/api/projects/${project.id}/story`,{method:'PUT',body:JSON.stringify(collectStory())});showProject(p);return p;}
function newProject(){if(dirty){toast('Save your draft before opening a new story.');return;}clearTimeout(poll);project=null;$('#welcome').hidden=false;$('#workspace').hidden=true;sessionStorage.removeItem('studio-project');refreshProjects();$('#prompt').focus();}
async function create(mode){
 const prompt=$('#prompt').value.trim();if(prompt.length<5){toast('Give your story a brief first.');$('#prompt').focus();return;}
 $('#draft-button').disabled=true;$('#manual-button').disabled=true;
 try{const p=await api('/api/projects',{method:'POST',body:JSON.stringify({prompt,mood:$('#mood').value,seconds:Number($('#seconds').value),mode})});showProject(p,'story');}
 finally{$('#draft-button').disabled=!config.ai_available;$('#manual-button').disabled=false;}
}
$('#brief-form').addEventListener('submit',e=>{e.preventDefault();action(()=>create('ai'));});
$('#manual-button').addEventListener('click',()=>action(()=>create('manual')));
$('#new-project').addEventListener('click',newProject);
$('#projects').addEventListener('click',e=>{const b=e.target.closest('[data-project]');if(b)action(async()=>{if(dirty)throw Error('Save your draft before switching stories.');const p=await api(`/api/projects/${b.dataset.project}`);showProject(p,p.video?'reel':'story');});});
$('#demo-button').addEventListener('click',()=>action(async()=>showProject(await api('/api/demo',{method:'POST'}),'reel')));
document.querySelectorAll('[data-step]').forEach(b=>b.addEventListener('click',()=>{if(dirty&&b.dataset.step!=='story'){toast('Save your story changes before switching steps.');return;}setStep(b.dataset.step);}));
$('#story-panel').addEventListener('input',()=>{dirty=true;updateWordCount();});
$('#scene-editor').addEventListener('click',e=>{const b=e.target.closest('[data-remove]');if(!b||busy(project))return;const story=collectStory();if(story.scenes.length<=3)return;story.scenes.splice(Number(b.dataset.remove),1);project.story=story;storyEditor();dirty=true;});
$('#add-scene').addEventListener('click',()=>{const story=collectStory();if(story.scenes.length>=8)return;story.scenes.push({narration:'',visual:'',movement:'breathe'});project.story=story;storyEditor();dirty=true;});
$('#save-story').addEventListener('click',()=>action(async()=>{await saveDraft();toast('Draft saved.');}));
$('#approve-story').addEventListener('click',()=>action(async()=>{await saveDraft();showProject(await api(`/api/projects/${project.id}/approve-story`,{method:'POST'}),'board');}));
$('#generate-images').addEventListener('click',()=>action(async()=>showProject(await api(`/api/projects/${project.id}/illustrate`,{method:'POST'}),'board')));
$('#approve-board').addEventListener('click',()=>action(async()=>showProject(await api(`/api/projects/${project.id}/approve-storyboard`,{method:'POST'}),'reel')));
$('#render-button').addEventListener('click',()=>action(async()=>showProject(await api(`/api/projects/${project.id}/render`,{method:'POST'}),'reel')));
$('#storyboard').addEventListener('change',e=>{const input=e.target.closest('[data-upload]');if(!input?.files[0])return;action(async()=>{
 const file=input.files[0];if(file.size>8*1024*1024)throw Error('Keep each file below 8 MB.');
 const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(Error('Could not read this file.'));reader.readAsDataURL(file);});
 showProject(await api(`/api/projects/${project.id}/scenes/${input.dataset.upload}/upload`,{method:'POST',body:JSON.stringify({data,kind:input.dataset.kind})}),'board');
});});
$('#setup-toggle').addEventListener('click',()=>{$('#setup').hidden=!$('#setup').hidden;if(!$('#setup').hidden)$('#setup').scrollIntoView({behavior:'smooth'});});$('#close-setup').addEventListener('click',()=>$('#setup').hidden=true);
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
(async()=>{try{
 config=await api('/api/config');$('#connection').textContent=config.ai_available?'AI generation connected':'Manual & example mode';$('#connection').classList.toggle('ready',config.ai_available);
 $('#draft-button').disabled=!config.ai_available;$('#ai-note').textContent=config.ai_available?'Story drafting uses your configured AI account. Illustrations wait for your approval.':'AI generation needs server setup. Try the finished example, or write your story and upload your own artwork.';
 $('#demo-button').disabled=!config.demo_available;$('#setup-status').textContent=`AI generation: ${config.ai_available?'configured':'needs an API key'}. Video renderer: ${config.ffmpeg?'ready':'FFmpeg needs installation'}.`;
 await refreshProjects();const saved=sessionStorage.getItem('studio-project');if(saved){try{const p=await api(`/api/projects/${saved}`);showProject(p,sessionStorage.getItem('studio-step')||(p.video?'reel':'story'));}catch{sessionStorage.removeItem('studio-project');}}
}catch(e){$('#connection').textContent='Connection unavailable';toast(e.message);}})();
