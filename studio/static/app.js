'use strict';
const $=s=>document.querySelector(s);
const escapeHTML=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const busy=p=>['drafting','illustrating','rendering'].includes(p?.status);
let config={},project=null,step='story',poll=null,dirty=false,requestPending=false;
async function api(path,options={}){
 const response=await fetch(path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
 const body=await response.json();
 if(!response.ok)throw Error(typeof body.detail==='string'?body.detail:Array.isArray(body.detail)?body.detail.map(e=>`${e.loc.slice(1).join(' → ')}: ${e.msg}`).join('; '):'Please check the fields and try again.');
 return body;
}
function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('#toast').hidden=true,4500);}
async function action(fn){if(requestPending)return;requestPending=true;try{await fn();}catch(e){toast(e.message);}finally{requestPending=false;}}
function fileURL(name){return `/api/projects/${project.id}/files/${encodeURIComponent(name)}?v=${encodeURIComponent(project.updated_at||'')}`;}
async function refreshProjects(){const projects=await api('/api/projects');$('#projects').innerHTML=projects.map(p=>`<button data-project="${p.id}" class="${project?.id===p.id?'selected':''}">${escapeHTML(p.title)}<small>${escapeHTML(p.status==='ready'?'Ready to watch':p.status)}</small></button>`).join('');}
function setStep(next){step=next;document.querySelectorAll('[data-step]').forEach(el=>el.classList.toggle('active',el.dataset.step===step));for(const key of ['story','board','reel'])$(`#${key}-panel`).hidden=key!==step;sessionStorage.setItem('studio-step',step);updateStageGuidance();}
function storyEditor(){
 const story=project.story;$('#story-title').value=story.title;$('#logline').value=story.logline;$('#character-notes').value=story.character_notes;
 $('#scene-editor').innerHTML=story.scenes.map((s,i)=>`<article class="scene-edit" data-scene="${i}"><div class="scene-top"><span>SCENE ${String(i+1).padStart(2,'0')}</span><button data-remove="${i}" ${story.scenes.length<=3?'disabled':''}>Remove</button></div><label>Narration<textarea data-field="narration" rows="4" maxlength="700">${escapeHTML(s.narration)}</textarea></label><label>What we see<textarea data-field="visual" rows="4" maxlength="2200">${escapeHTML(s.visual)}</textarea></label><label class="motion-label">Character movement<select data-field="movement"><option value="breathe" ${s.movement==='breathe'?'selected':''}>Subtle breathing</option><option value="nod" ${s.movement==='nod'?'selected':''}>A gentle nod</option><option value="still" ${s.movement==='still'?'selected':''}>Hold the pose</option></select></label></article>`).join('');
 animationEditors();updateWordCount();enhanceSceneEditor();
}
function collectStory(){
 const result={...project.story,title:$('#story-title').value.trim(),logline:$('#logline').value.trim(),character_notes:$('#character-notes').value.trim(),scenes:[...document.querySelectorAll('.scene-edit')].map((card,i)=>({...project.story.scenes[i],...Object.fromEntries(['narration','visual','movement'].map(key=>[key,card.querySelector(`[data-field="${key}"]`).value.trim()]))}))};
 if(result.render_style==='rigged'){
  result.voice=$('#voice-choice').value;
  result.characters=[...document.querySelectorAll('[data-character-id]')].map(el=>({id:el.dataset.characterId,...Object.fromEntries([...el.querySelectorAll('[data-character]')].map(input=>[input.dataset.character,input.value]))}));
  result.scenes.forEach((scene,i)=>scene.animation=readAnimation(document.querySelector(`[data-scene="${i}"]`)));
 }
 return result;
}
function updateWordCount(){const text=[...document.querySelectorAll('[data-field="narration"]')].map(el=>el.value).join(' ').trim();$('#word-count').textContent=`${text?text.split(/\s+/).length:0} spoken words`;}
function drawBoard(){
 const rigged=project.story.render_style==='rigged';
 $('#storyboard').innerHTML=project.story.scenes.map((s,i)=>{const image=project.assets[String(i)];return `<article class="board-card"><div class="board-image">${image?`<img src="${fileURL(image.file)}" alt="Scene ${i+1} illustration">`:'Awaiting illustration'}<b>SCENE ${i+1}</b></div><div class="board-copy">${rigged?`<div class="motion-preview"><button data-play="${i}" class="secondary" ${!project.story_approval?'disabled':''}>Play action preview</button><label>Scrub movement<input type="range" min="0" max="6" step="0.1" value="0" data-scrub="${i}" ${!project.story_approval?'disabled':''}></label><small>6-second silent preview; final timing follows the voice.</small>${project.backgrounds?.[i]?`<button class="text-button" data-reset-bg="${i}">Restore preset background</button>`:''}</div>`:''}<p>${escapeHTML(s.narration)}</p><details><summary>Visual direction</summary><p>${escapeHTML(s.visual)}</p></details><label class="upload-label">${rigged?'Upload background':image?'Replace illustration':'Upload illustration'}<input type="file" accept="image/png,image/jpeg,image/webp" data-upload="${i}" data-kind="${rigged?'background':'image'}" ${busy(project)||!project.story_approval?'disabled':''}></label><label class="upload-label">${project.audio[String(i)]?'Voice recording added ✓':'Use your own voice (optional)'}<input type="file" accept="audio/*" data-upload="${i}" data-kind="audio" ${busy(project)||!project.story_approval?'disabled':''}></label></div></article>`;}).join('');
 const missing=project.story.scenes.length-Object.keys(project.assets).length;
 $('#generate-images').textContent=rigged?'Build animated scenes':missing?`Generate ${missing} illustration${missing===1?'':'s'} ↗`:'All illustrations added ✓';
 $('#generate-images').disabled=busy(project)||!project.story_approval||(!rigged&&(!config.image_available||!missing));
 $('#image-cost').textContent=rigged?'Built-in characters and backgrounds are free. Preview the actions, or upload your own background below.':!project.story_approval?'Approve your story first.':missing?`Generating creates ${missing} new image${missing===1?'':'s'} and may incur AI provider charges. Existing images are reused. You can upload your own instead.`:'Review all the illustrations. You can replace any scene with an upload.';
 $('#approve-board').disabled=busy(project)||!project.story_approval||missing>0;
}
function showProject(p,nextStep){
 project=p;dirty=false;clearTimeout(poll);$('#welcome').hidden=true;$('#workspace').hidden=false;
 $('#project-title').textContent=p.story?.title||'Writing your story…';$('#reel-title').textContent=p.story?.title||'';
 $('#job-status').textContent=p.progress;$('#job-status').classList.toggle('busy',busy(p));$('#error').hidden=!p.error;$('#error').textContent=p.error||'';
 $('#download-story').href=fileURL('story.json');$('#download-story').hidden=!p.story;
 if(p.story){
  storyEditor();drawBoard();
  $('#story-approval-note').textContent=p.story_approval?'Story approved. Editing it resets the storyboard and approvals.':'Approve the story before building its scenes.';
  $('#save-story').disabled=busy(p);$('#approve-story').disabled=busy(p);$('#add-scene').disabled=busy(p)||p.story.scenes.length>=8;
  document.querySelectorAll('#story-panel input,#story-panel textarea,#story-panel select,#story-panel [data-remove]').forEach(el=>el.disabled=busy(p)||(el.hasAttribute('data-remove')&&p.story.scenes.length<=3));
 }
 document.querySelector('[data-step="story"]').classList.toggle('done',!!p.story_approval);document.querySelector('[data-step="board"]').classList.toggle('done',!!p.storyboard_approval);document.querySelector('[data-step="reel"]').classList.toggle('done',!!p.video);
 $('#render-button').disabled=busy(p)||!p.storyboard_approval||!config.ffmpeg;$('#render-button').hidden=!!p.video;
 $('#download-video').hidden=!p.video;$('#download-captions').hidden=!p.captions;
 if(p.video){$('#video-container').innerHTML=`<video controls playsinline preload="metadata" src="${fileURL(p.video)}"></video>`;$('#download-video').href=fileURL(p.video);$('#download-captions').href=fileURL(p.captions);$('#reel-detail').textContent=`${Math.round(p.duration||0)} seconds · 720 × 1280 · MP4`;}
 else{$('#video-container').innerHTML='<div class="video-placeholder">Your reel will appear here.<small>Approve your story and storyboard first.</small></div>';$('#reel-detail').textContent='Your narration sets the pace. We leave space for the expressions and ending.';}
 refreshFlow(p);setStep(nextStep||step);if(!p.story)$('#story-panel').hidden=true;
 sessionStorage.setItem('studio-project',p.id);refreshProjects().catch(e=>toast(e.message));
 if(busy(p))poll=setTimeout(async()=>{try{showProject(await api(`/api/projects/${p.id}`));}catch(e){toast(e.message);}},1800);
}
async function saveDraft(){if(!project?.story)return;const p=await api(`/api/projects/${project.id}/story`,{method:'PUT',body:JSON.stringify(collectStory())});showProject(p);return p;}
function newProject(){if(dirty){toast('Save your draft before opening a new story.');return;}clearTimeout(poll);project=null;$('#welcome').hidden=false;$('#workspace').hidden=true;sessionStorage.removeItem('studio-project');refreshProjects();$('#prompt').focus();}
async function create(mode){
 const prompt=$('#prompt').value.trim();if(prompt.length<5){toast('Give your story a brief first.');$('#prompt').focus();return;}
 $('#draft-button').disabled=true;$('#manual-button').disabled=true;
 try{const p=await api('/api/projects',{method:'POST',body:JSON.stringify({prompt,mood:$('#mood').value,seconds:Number($('#seconds').value),render_style:$('#render-style').value,mode})});showProject(p,'story');}
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
$('#add-scene').addEventListener('click',()=>{const story=collectStory();if(story.scenes.length>=8)return;story.scenes.push({narration:'',visual:'',movement:'still',animation:story.render_style==='rigged'?defaultAnimation():null});project.story=story;storyEditor();dirty=true;});
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
function toggleSetup(){$('#setup').hidden=!$('#setup').hidden;if(!$('#setup').hidden)$('#setup').scrollIntoView({behavior:'smooth'});}
$('#setup-toggle').addEventListener('click',toggleSetup);$('#setup-mobile').addEventListener('click',toggleSetup);$('#close-setup').addEventListener('click',()=>$('#setup').hidden=true);
window.addEventListener('beforeunload',e=>{if(dirty){e.preventDefault();e.returnValue='';}});
(async()=>{try{
 config=await api('/api/config');$('#connection').textContent=config.ai_available?`${config.story_provider==='anthropic'?'Anthropic':'OpenAI'} story drafting · configured`:'Animation studio · ready';$('#connection').classList.toggle('ready',config.ai_available);
 $('#draft-button').disabled=!config.ai_available;$('#ai-note').textContent=config.ai_available?`Story drafting uses ${config.story_provider==='anthropic'?'Anthropic':'OpenAI'}. Animated characters and preset backgrounds render locally. New AI illustrations require OpenAI or your own uploads.`:'Write your own story or try the editable example—animation needs no AI credits. To enable Draft my story, add ANTHROPIC_API_KEY or OPENAI_API_KEY on the server and restart. See Setup.';
 $('#demo-button').disabled=!config.demo_available;$('#setup-status').textContent=`Story drafting: ${config.ai_available?config.story_provider+' configured':'needs an Anthropic or OpenAI key'}. New AI illustrations: ${config.image_available?'OpenAI configured':'upload your own or add an OpenAI key'}. Video renderer: ${config.ffmpeg?'ready':'FFmpeg needs installation'}.`;
 await refreshProjects();const saved=sessionStorage.getItem('studio-project');if(saved){try{const p=await api(`/api/projects/${saved}`);showProject(p,sessionStorage.getItem('studio-step')||(p.video?'reel':'story'));}catch{sessionStorage.removeItem('studio-project');}}
}catch(e){$('#connection').textContent='Connection unavailable';toast(e.message);}})();

// Each stage has a different review task: read, inspect one scene, then watch.
let flowProjectId=null,storyView='read',focusedScene=0;
function updateStageGuidance(){
 if(!project)return;
 const copy={story:['01 / THE STORY','Read it as a story first. Edit only the moments that need work.'],board:['02 / THE SCENES','Give each moment your attention. Follow the images from beginning to end.'],reel:['03 / THE REEL',project.video?'Watch the whole story. Let the ending land.':'Your story and scenes are together. Now give them a voice.']};
 const [label,text]=copy[step];$('#stage-guidance').innerHTML=`<span>${label}</span><p>${text}</p>`;
}
function enhanceSceneEditor(){
 document.querySelectorAll('.scene-edit').forEach((card,i)=>{
  const top=card.querySelector('.scene-top');
  const toggle=document.createElement('button');toggle.type='button';toggle.className='scene-expand';toggle.dataset.expand=String(i);toggle.textContent='Edit scene';toggle.setAttribute('aria-expanded',String(i===0));top.prepend(toggle);
  const preview=document.createElement('p');preview.className='scene-summary';preview.textContent=card.querySelector('[data-field="narration"]').value||'Give this moment a beginning.';top.after(preview);card.classList.toggle('expanded',i===0);
 });
 refreshReader();
}
function refreshReader(){
 if(!project?.story)return;
 const s=collectStory();
 $('#story-read-pane').innerHTML=`<div class="reader-heading"><span class="kicker">THE COMPLETE STORY</span><h3>${escapeHTML(s.title)}</h3><p>${escapeHTML(s.logline)}</p></div><div class="reader-pages">${s.scenes.map((scene,i)=>`<div class="reader-beat"><button type="button" data-edit-beat="${i}" aria-label="Edit scene ${i+1}">${String(i+1).padStart(2,'0')} ↗</button><p>${escapeHTML(scene.narration||'This moment is waiting to be written.')}</p></div>`).join('')}</div>`;
}
function chooseStoryView(view){storyView=view;$('#story-read-pane').hidden=view!=='read';$('#story-edit-pane').hidden=view!=='edit';document.querySelectorAll('[data-story-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.storyView===view)));if(view==='read')refreshReader();}
function focusBoard(index,bringIntoView=false){
 if(!project?.story)return;
 const scenes=project.story.scenes;focusedScene=Math.max(0,Math.min(scenes.length-1,index));
 document.querySelectorAll('#storyboard .board-card').forEach((card,i)=>card.hidden=i!==focusedScene);
 document.querySelectorAll('[data-board-scene]').forEach((button,i)=>button.setAttribute('aria-current',i===focusedScene?'step':'false'));
 $('#previous-scene').disabled=focusedScene===0;$('#next-scene').disabled=focusedScene===scenes.length-1;
 $('#board-position').textContent=`${focusedScene+1} of ${scenes.length}`;
 const phase=focusedScene===0?'THE OPENING':focusedScene===scenes.length-1?'THE ENDING':'THE STORY UNFOLDS';
 $('#board-focus-label').textContent=`SCENE ${String(focusedScene+1).padStart(2,'0')} / ${phase}`;
 if(bringIntoView)$('#board-focus-label').scrollIntoView({behavior:'smooth',block:'start'});
}
function refreshFlow(p){
 if(!p.story){$('#stage-guidance').hidden=true;return;}
 $('#stage-guidance').hidden=false;
 if(flowProjectId!==p.id){flowProjectId=p.id;focusedScene=0;storyView=p.story.scenes.some(s=>!s.narration)?'edit':'read';}
 chooseStoryView(storyView);
 $('#board-filmstrip').innerHTML=p.story.scenes.map((scene,i)=>{const asset=p.assets[String(i)];return `<button type="button" data-board-scene="${i}" aria-label="Review scene ${i+1}">${asset?`<img src="${fileURL(asset.file)}" alt="">`:'<span class="empty-thumb">＋</span>'}<b>${String(i+1).padStart(2,'0')}</b></button>`;}).join('');
 focusBoard(focusedScene);
}
$('.story-view-switch').addEventListener('click',e=>{const b=e.target.closest('[data-story-view]');if(b)chooseStoryView(b.dataset.storyView);});
$('#story-read-pane').addEventListener('click',e=>{const b=e.target.closest('[data-edit-beat]');if(!b)return;chooseStoryView('edit');document.querySelectorAll('.scene-edit').forEach((card,i)=>{const active=i===Number(b.dataset.editBeat);card.classList.toggle('expanded',active);card.querySelector('[data-expand]').setAttribute('aria-expanded',String(active));});document.querySelector(`[data-scene="${b.dataset.editBeat}"]`).scrollIntoView({behavior:'smooth',block:'center'});});
$('#scene-editor').addEventListener('click',e=>{const b=e.target.closest('[data-expand]');if(!b)return;const card=b.closest('.scene-edit');card.classList.toggle('expanded');b.setAttribute('aria-expanded',String(card.classList.contains('expanded')));});
$('#scene-editor').addEventListener('input',e=>{const card=e.target.closest('.scene-edit');if(card)card.querySelector('.scene-summary').textContent=card.querySelector('[data-field="narration"]').value||'Give this moment a beginning.';});
$('#board-filmstrip').addEventListener('click',e=>{const b=e.target.closest('[data-board-scene]');if(b)focusBoard(Number(b.dataset.boardScene),true);});
$('#previous-scene').addEventListener('click',()=>focusBoard(focusedScene-1,true));$('#next-scene').addEventListener('click',()=>focusBoard(focusedScene+1,true));
$('.idea-starters').addEventListener('click',e=>{const b=e.target.closest('[data-starter]');if(!b)return;const starters={kindness:['A reserved person makes one small gesture for a stranger, and discovers the connection mattered to them both.','heartwarming'],comedy:['Someone tries to make an ordinary morning run perfectly. One tiny mistake keeps making things funnier, until they learn to laugh at it.','funny'],mystery:['A person notices the same small object appearing at their doorstep each morning, and follows the clues to a simple, surprising explanation.','suspense']};const [prompt,mood]=starters[b.dataset.starter];$('#prompt').value=prompt;$('#mood').value=mood;$('#prompt').focus();});

$('#add-scene').addEventListener('click',()=>{if(busy(project))return;const cards=[...document.querySelectorAll('.scene-edit')];cards.forEach((card,i)=>{const active=i===cards.length-1;card.classList.toggle('expanded',active);card.querySelector('[data-expand]').setAttribute('aria-expanded',String(active));});cards.at(-1)?.scrollIntoView({behavior:'smooth',block:'center'});});

function options(values,selected){return values.map(v=>`<option value="${escapeHTML(v)}" ${v===selected?'selected':''}>${escapeHTML(v.replaceAll('_',' '))}</option>`).join('');}
function selectField(label,key,values,value,attribute='anim'){return `<label>${label}<select data-${attribute}="${key}">${options(values,value)}</select></label>`;}
function defaultAnimation(){return {setting:'cafe',light:'day',speaker:'narrator',time_jump:false,actors:[{character:'hero',x:.32,end_x:.65,pose:'standing',action:'idle',emotion:'neutral',prop:''}],props:[]};}
function animationEditors(){
 const rigged=project.story.render_style==='rigged';$('#character-editor').innerHTML='';
 if(!rigged)return;
 const chars=project.story.characters;
 $('#character-editor').innerHTML=`<details class="character-controls"><summary>Characters & narrator</summary><p class="hint">Character identities stay consistent throughout the story.</p>${chars.map(c=>`<div class="control-grid" data-character-id="${c.id}"><label>${escapeHTML(c.id)} name<input data-character="name" maxlength="40" value="${escapeHTML(c.name)}"></label><label>Body color<input data-character="color" type="color" value="${c.color}"></label><label>Accent<input data-character="accent" type="color" value="${c.accent}"></label>${selectField('Accessory','accessory',['none','scarf','glasses'],c.accessory,'character')}</div>`).join('')}<label>Narrator<select id="voice-choice">${options(['en-US-AndrewNeural','en-US-AvaNeural','en-GB-SoniaNeural'],project.story.voice)}</select></label></details>`;
 document.querySelectorAll('.scene-edit').forEach((card,i)=>{
  card.querySelector('.motion-label').hidden=true;
  const a=project.story.scenes[i].animation||defaultAnimation();
  const div=document.createElement('div');div.className='animation-controls';
  div.innerHTML=`<h3>Direct this scene</h3><div class="control-grid">${selectField('Background','setting',config.animation.settings,a.setting)}${selectField('Lighting','light',['day','sunset','night'],a.light)}${selectField('Voice belongs to','speaker',['narrator',...chars.map(c=>c.id)],a.speaker)}<label class="check-label"><input type="checkbox" data-anim="time_jump" ${a.time_jump?'checked':''}> A new moment in time</label></div><p class="hint">One action per character. Start and end controls place it alongside the narration; the character rests before and after. Add another scene for the next action.</p><div class="actor-controls">${a.actors.map(actor=>actorControl(actor,chars)).join('')}</div><button type="button" class="text-button" data-add-actor ${a.actors.length>=chars.length?'disabled':''}>＋ Add second character</button><h4>Objects in this scene</h4><div class="prop-controls">${a.props.map(propControl).join('')}</div><button type="button" class="text-button" data-add-prop>＋ Add object</button>`;
  card.append(div);
 });
}
function actorControl(a,chars){return `<fieldset data-actor="${a.character}"><legend>${escapeHTML(chars.find(c=>c.id===a.character)?.name||a.character)}</legend><div class="control-grid">${selectField('Action','action',config.animation.actions,a.action,'actor-field')}${selectField('Emotion','emotion',config.animation.emotions,a.emotion,'actor-field')}${selectField('Emotion at the end','end_emotion',['',...config.animation.emotions],a.end_emotion||'','actor-field')}${selectField('Starting pose','pose',['standing','seated'],a.pose||'standing','actor-field')}<label>Position (% from left)<input data-actor-field="x" type="number" min="18" max="82" step="1" value="${Math.round(a.x*100)}"></label><label>Walk destination (%)<input data-actor-field="end_x" type="number" min="18" max="82" step="1" value="${Math.round(a.end_x*100)}"></label><label>Action starts (% of scene)<input data-actor-field="action_start" type="number" min="0" max="85" step="1" value="${Math.round((a.action_start??0)*100)}"></label><label>Action ends (%)<input data-actor-field="action_end" type="number" min="15" max="100" step="1" value="${Math.round((a.action_end??1)*100)}"></label><label>Object name for action<input data-actor-field="prop" maxlength="30" value="${escapeHTML(a.prop)}" placeholder="e.g. gift"></label></div><button type="button" class="text-button" data-remove-actor>Remove character</button></fieldset>`;}
function propControl(p){return `<fieldset data-prop><div class="control-grid"><label>Object name<input data-prop-field="id" value="${escapeHTML(p.id)}" maxlength="30"></label>${selectField('Object','kind',config.animation.props,p.kind,'prop-field')}<label>Table position (%)<input data-prop-field="x" type="number" min="12" max="88" step="1" value="${Math.round(p.x*100)}"></label>${selectField('Starts held by','holder',['none','hero','friend'],p.holder,'prop-field')}</div><button type="button" class="text-button" data-remove-prop>Remove object</button></fieldset>`;}
function readAnimation(card){
 const a={};card.querySelectorAll('[data-anim]').forEach(el=>a[el.dataset.anim]=el.type==='checkbox'?el.checked:el.value);
 a.actors=[...card.querySelectorAll('[data-actor]')].map(el=>({character:el.dataset.actor,...Object.fromEntries([...el.querySelectorAll('[data-actor-field]')].map(input=>[input.dataset.actorField,input.type==='number'?Number(input.value)/100:input.dataset.actorField==='end_emotion'?(input.value||null):input.value]))}));
 a.props=[...card.querySelectorAll('[data-prop]')].map(el=>Object.fromEntries([...el.querySelectorAll('[data-prop-field]')].map(input=>[input.dataset.propField,input.type==='number'?Number(input.value)/100:input.value])));
 return a;
}
$('#scene-editor').addEventListener('click',e=>{
 const card=e.target.closest('.scene-edit');if(!card||busy(project))return;
 if(e.target.matches('[data-add-actor]')){const used=[...card.querySelectorAll('[data-actor]')].map(el=>el.dataset.actor);const c=project.story.characters.find(c=>!used.includes(c.id));if(c)card.querySelector('.actor-controls').insertAdjacentHTML('beforeend',actorControl({...defaultAnimation().actors[0],character:c.id,x:.65,end_x:.32},project.story.characters));e.target.disabled=true;dirty=true;}
 if(e.target.matches('[data-remove-actor]')&&card.querySelectorAll('[data-actor]').length>1){e.target.closest('[data-actor]').remove();card.querySelector('[data-add-actor]').disabled=false;dirty=true;}
 if(e.target.matches('[data-add-prop]')&&card.querySelectorAll('[data-prop]').length<5){card.querySelector('.prop-controls').insertAdjacentHTML('beforeend',propControl({id:'object'+(card.querySelectorAll('[data-prop]').length+1),kind:'book',x:.5,holder:'none'}));dirty=true;}
 if(e.target.matches('[data-remove-prop]')){e.target.closest('[data-prop]').remove();dirty=true;}
});
$('#animated-example').addEventListener('click',()=>action(async()=>showProject(await api('/api/animated-example',{method:'POST'}),'story')));
$('#import-story').addEventListener('change',e=>action(async()=>{const file=e.target.files[0];if(!file)return;if(file.size>100000)throw Error('Story JSON must be below 100 KB.');const story=JSON.parse(await file.text());showProject(await api('/api/projects/import',{method:'POST',body:JSON.stringify(story)}),'story');e.target.value='';}));
let playback=0;
function previewFrame(index,t){const img=document.querySelectorAll('.board-card')[index]?.querySelector('.board-image img');if(img)img.src=`/api/projects/${project.id}/scenes/${index}/frame?t=${Number(t).toFixed(2)}`;return img;}
$('#storyboard').addEventListener('input',e=>{if(e.target.matches('[data-scrub]')){playback++;previewFrame(Number(e.target.dataset.scrub),e.target.value);}});
$('#storyboard').addEventListener('click',e=>{
 const play=e.target.closest('[data-play]');if(play){const token=++playback;const id=project.id,index=Number(play.dataset.play);let t=0;play.textContent='Playing…';const tick=()=>{if(token!==playback||project?.id!==id||step!=='board'||focusedScene!==index){play.textContent='Play action preview';return;}const img=previewFrame(index,t);if(!img){play.textContent='Build scenes first';return;}const slider=play.closest('.motion-preview').querySelector('input');slider.value=t;img.onload=()=>{img.onload=null;if(t<6){t=Math.min(6,t+.16);setTimeout(tick,100);}else play.textContent='Play action preview';};img.onerror=()=>{play.textContent='Preview unavailable';};};tick();}
 const reset=e.target.closest('[data-reset-bg]');if(reset)action(async()=>showProject(await api(`/api/projects/${project.id}/scenes/${reset.dataset.resetBg}/background`,{method:'DELETE'}),'board'));
});
