/* Run against tests/serve_chat_fixture.py, never the user's model server.
   PLAYWRIGHT_MODULE can point at a bundled Playwright installation. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const base=process.env.INFLECT_TEST_URL||'http://127.0.0.1:7861';
const output=process.env.INFLECT_TEST_OUTPUT||'/tmp';
const headers={'X-Inflect-Local':'1'};
function pdfBytes(){
 const objects=['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Kids [3 0 R] /Count 1 >>','<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 400] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>','<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>'];
 const stream='BT /F1 20 Tf 40 320 Td (Inflect PDF test document) Tj ET';objects.push(`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`);
 let text='%PDF-1.4\n';const offsets=[0];objects.forEach((obj,i)=>{offsets.push(text.length);text+=`${i+1} 0 obj\n${obj}\nendobj\n`});const xref=text.length;text+=`xref\n0 ${objects.length+1}\n0000000000 65535 f \n`;offsets.slice(1).forEach(n=>text+=`${String(n).padStart(10,'0')} 00000 n \n`);text+=`trailer\n<< /Size ${objects.length+1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;return Buffer.from(text);
}
(async()=>{
 const browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1600,height:1000}});const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(String(e)));
 const input=()=>page.getByRole('textbox',{name:'Message',exact:true});
 const settled=async()=>{await page.waitForFunction(()=>{const el=document.querySelector('textarea[aria-label="Message"]');return el&&!el.disabled});};
 const newChat=async()=>{await page.locator('button[aria-label="New conversation"]').click();await settled();await page.waitForFunction(()=>document.querySelector('textarea[aria-label="Message"]')?.value==='')};
 const send=async(text)=>{await input().fill(text);await page.getByRole('button',{name:'Send message',exact:true}).click();await page.getByRole('button',{name:'Stop generation',exact:true}).waitFor();await page.getByRole('button',{name:'Stop generation',exact:true}).waitFor({state:'hidden'});await settled()};
 await page.goto(base);await settled();if(await page.getByRole('button',{name:'Dismiss model ready notification'}).count())await page.getByRole('button',{name:'Dismiss model ready notification'}).click();await newChat();
 await page.screenshot({path:output+'/inflect-dashboard.png'});
 await send('Create an interactive HTML demo.');await page.getByRole('button',{name:'Maximize chat',exact:true}).click();await page.getByRole('button',{name:'Preview',exact:true}).click();
 const frame=page.frameLocator('iframe[title^="Preview of"]');await frame.getByRole('button',{name:'Take a step'}).click();await frame.getByText('1 steps forward').waitFor();
 const iframe=page.frames().find(f=>f!==page.mainFrame());assert.equal(await iframe.evaluate(()=>{try{parent.document.title;return 'unsafe'}catch{return 'isolated'}}),'isolated');
 await page.waitForTimeout(1600);await page.screenshot({path:output+'/inflect-expanded-preview.png'});
 await page.getByRole('button',{name:'Close preview',exact:true}).click();await page.getByRole('button',{name:'Edit',exact:true}).first().click();await page.getByRole('textbox',{name:'Edit message'}).fill('Edited HTML request');await page.getByRole('button',{name:'Save branch & send'}).click();await page.getByText('Edited HTML request',{exact:true}).waitFor();await settled();
 await page.getByRole('button',{name:'Retry',exact:true}).last().click();await page.getByRole('button',{name:'Stop generation',exact:true}).waitFor();await settled();
 await page.getByRole('button',{name:'Branch',exact:true}).last().click();await settled();await page.reload();await settled();await page.getByText('Edited HTML request',{exact:true}).waitFor();
 await newChat();await input().fill('A durable draft');await page.waitForTimeout(900);await page.reload();await settled();assert.equal(await input().inputValue(),'A durable draft');
 await page.getByRole('button',{name:'Open conversation history'}).click();await page.getByRole('textbox',{name:'Search conversations'}).fill('interactive HTML demo');await page.getByRole('button',{name:/Create an interactive HTML demo/}).first().click();await settled();
 await page.getByRole('button',{name:'Conversation options'}).click();const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('button',{name:'Export conversation',exact:true}).click()]);await download.saveAs(output+'/inflect-roundtrip.json');
 await page.locator('input[type=file][accept=".json,application/json"]').setInputFiles(output+'/inflect-roundtrip.json');await settled();await page.getByText(JSON.parse(fs.readFileSync(output+'/inflect-roundtrip.json','utf8')).conversation.messages[0].content,{exact:true}).last().waitFor();
 await newChat();const files=page.locator('input[type=file][multiple]');
 const attach=async(name,mime,buffer)=>{await files.setInputFiles({name,mimeType:mime,buffer});await page.getByRole('button',{name:`Preview ${name}`,exact:true}).waitFor();await settled()};
 await attach('notes.md','text/markdown',Buffer.from('# A local document\n\n**Markdown** preview.'));await page.getByRole('button',{name:'Preview notes.md'}).click();await page.getByRole('heading',{name:'A local document'}).waitFor();await page.getByRole('button',{name:'Close preview'}).click();
 await attach('sample.pdf','application/pdf',pdfBytes());await page.getByRole('button',{name:'Preview sample.pdf'}).click();await page.waitForFunction(()=>{const c=document.querySelector('.pdf-preview canvas');return c&&c.width>0&&c.height>0});await page.getByText('Page 1 of 1').waitFor();await page.getByRole('button',{name:'Close preview'}).click();
 await attach('mark.svg','image/svg+xml',Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80"><circle cx="40" cy="40" r="30" fill="green"/></svg>'));await page.getByRole('button',{name:'Preview mark.svg'}).click();await page.frameLocator('iframe').locator('circle').waitFor();await page.getByRole('button',{name:'Close preview'}).click();
 await attach('pixel.png','image/png',Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==','base64'));await page.getByRole('button',{name:'Preview pixel.png'}).click();await page.locator('.image-preview img').waitFor();await page.getByRole('button',{name:'Close preview'}).click();
 await attach('tone.wav','audio/wav',Buffer.from('RIFF0000WAVEfmt '));await page.getByRole('button',{name:'Preview tone.wav'}).click();await page.locator('audio[controls]').waitFor();await page.getByRole('button',{name:'Close preview'}).click();await input().fill('Listen to these attachments');await page.getByRole('button',{name:'Send message',exact:true}).click();await page.getByRole('alert').filter({hasText:'transcript'}).waitFor();assert.equal(await input().inputValue(),'Listen to these attachments');
 await page.getByRole('button',{name:'Remove tone.wav'}).click();await settled();await send('Summarize the attached documents.');
 await newChat();await input().fill('slow response');await page.getByRole('button',{name:'Send message',exact:true}).click();await page.getByText('Part 0.',{exact:false}).waitFor();await page.getByRole('button',{name:'Stop generation',exact:true}).click();await page.getByText('Stopped · partial answer saved',{exact:false}).waitFor();await settled();
 await newChat();await input().fill('slow disconnect recovery');await page.getByRole('button',{name:'Send message',exact:true}).click();await page.getByText('Part 0.',{exact:false}).waitFor();await page.reload();await settled();await page.getByText('Stopped · partial answer saved',{exact:false}).waitFor();
 await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'Maximize chat',exact:true}).click();await page.screenshot({path:output+'/inflect-mobile.png'});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 await page.setViewportSize({width:1600,height:1000});
 const current=await page.evaluate(()=>localStorage.getItem('inflect.active-chat'));const second=await context.newPage();await second.goto(base);await second.getByText('slow disconnect recovery',{exact:true}).last().waitFor();
 await page.getByRole('button',{name:'Conversation options'}).click();await page.getByRole('button',{name:'Delete permanently',exact:true}).click();await page.getByRole('dialog').getByRole('button',{name:'Delete permanently',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});
 assert.equal((await page.request.get(base+'/api/conversations/'+current)).status(),404);await second.getByRole('alert').filter({hasText:'permanently deleted in another window'}).waitFor();
 assert.deepEqual(errors,[]);console.log(JSON.stringify({result:'passed',checks:'HTML interactivity and isolation, edit, retry, branch, drafts/reload, search, export/import, Markdown/PDF/SVG/image/audio previews, capability rejection, stop and disconnect recovery, mobile overflow, permanent deletion across windows',screenshots:output},null,2));await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
