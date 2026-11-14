import http from 'node:http';
import {randomUUID} from 'node:crypto';
import pg from 'pg';
const pool=new pg.Pool({connectionString:process.env.DATABASE_URL,ssl:{rejectUnauthorized:false}});
await pool.query(`CREATE TABLE IF NOT EXISTS trees(id text primary key,name text not null,mood text not null,questions jsonb not null);
CREATE TABLE IF NOT EXISTS ornaments(id text primary key,tree_id text references trees(id),name text,color text,score int default 0,step int default 0,choices text default '',created_at bigint not null);
CREATE INDEX IF NOT EXISTS ornaments_results ON ornaments(tree_id,step,created_at DESC)`);
const clean=(v,n)=>typeof v==='string'?v.trim().slice(0,n):'';
const respond=(res,data,status=200)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(data))};
const err=(res,msg,status=400)=>respond(res,{error:msg},status);
const colors=['cranberry','gold','ice','rose'];
async function body(req){let chunks=[],n=0;for await(const c of req){n+=c.length;if(n>24576)throw Error('请求太长');chunks.push(c)}return JSON.parse(Buffer.concat(chunks).toString())}
async function api(req,res,path){
 const method=req.method,b=method==='POST'?await body(req):{};
 if(path==='/api/trees'&&method==='POST'){
  const name=clean(b.name,24),mood=clean(b.mood,36),questions=b.questions;
  if(!name||!Array.isArray(questions)||questions.length!==10)return err(res,'需要名字和十道题');
  const qs=questions.map(q=>({prompt:clean(q?.prompt,90),options:Array.isArray(q?.options)?q.options.map(o=>clean(o,50)):[],answer:q?.answer}));
  if(qs.some(q=>!q.prompt||q.options.length!==3||q.options.some(o=>!o)||new Set(q.options).size!==3||![0,1,2].includes(q.answer)))return err(res,'题目格式无效');
  const id=randomUUID().replaceAll('-','');await pool.query('INSERT INTO trees VALUES($1,$2,$3,$4)',[id,name,mood,JSON.stringify(qs)]);return respond(res,{id},201);
 }
 const m=/^\/api\/trees\/([a-f0-9]{32})(?:\/(start|answer))?$/.exec(path);
 if(!m)return err(res,'未找到',404);
 const [_,id,action]=m;
 const {rows:[tree]}=await pool.query('SELECT * FROM trees WHERE id=$1',[id]);
 if(!tree)return err(res,'圣诞树不存在',404);
 const qs=tree.questions;
 if(!action&&method==='GET'){
  const [recent,total]=await Promise.all([pool.query('SELECT name,color,score FROM ornaments WHERE tree_id=$1 AND step=10 ORDER BY score DESC,created_at DESC LIMIT 40',[id]),pool.query('SELECT count(*)::int AS count FROM ornaments WHERE tree_id=$1 AND step=10',[id])]);
  return respond(res,{name:tree.name,mood:tree.mood,count:total.rows[0].count,ornaments:recent.rows,questions:qs.map(({prompt,options})=>({prompt,options}))});
 }
 if(action==='start'&&method==='POST'){
  const name=clean(b.name,24);if(!name||!colors.includes(b.color))return err(res,'请填写名字并选球');
  const attempt=randomUUID().replaceAll('-','');await pool.query('INSERT INTO ornaments(id,tree_id,name,color,created_at) VALUES($1,$2,$3,$4,$5)',[attempt,id,name,b.color,Date.now()]);return respond(res,{attempt},201);
 }
 if(action==='answer'&&method==='POST'){
  const {attempt,step,choice}=b;
  if(!/^[a-f0-9]{32}$/.test(attempt||'')||!Number.isInteger(step)||step<0||step>9||![0,1,2].includes(choice))return err(res,'无效答案');
  const correct=qs[step].answer;
  const update=await pool.query("UPDATE ornaments SET step=step+1,score=score+$1,choices=choices||$2 WHERE id=$3 AND tree_id=$4 AND step=$5 RETURNING score",[Number(choice===correct),String(choice),attempt,id,step]);
  if(update.rows.length)return respond(res,{correct,score:update.rows[0].score});
  const old=await pool.query('SELECT score,step,choices FROM ornaments WHERE id=$1 AND tree_id=$2',[attempt,id]);
  if(old.rows[0]?.step===step+1&&old.rows[0]?.choices[step]===String(choice))return respond(res,{correct,score:old.rows[0].score});
  return err(res,'此题已提交',409);
 }
 return err(res,'不支持此操作',405);
}
const html=`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>冬夜挂球 · Winter Wishes</title><style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(ellipse at 50% 10%,#274b49 0%,#0d2229 48%,#07121d 100%);font-family:ui-sans-serif,-apple-system,"PingFang SC",sans-serif;color:#fff4df;text-align:center}main{max-width:540px;margin:auto;padding:32px 20px 70px}h1{font-family:Georgia,serif;font-size:clamp(34px,8vw,55px);font-weight:400;margin:10px 0}p{line-height:1.8;color:#e4d9c3}.eyebrow{letter-spacing:.28em;color:#e8c28d;font-size:12px}.tree{font-size:125px;filter:drop-shadow(0 0 35px #a9a05b55);animation:float 4s ease-in-out infinite}@keyframes float{50%{transform:translateY(-8px)}}.panel{background:#ffffff0c;border:1px solid #e5cb9b55;backdrop-filter:blur(18px);border-radius:24px;padding:26px;margin-top:20px;box-shadow:0 25px 80px #0004}input,textarea,button{font:inherit}input,textarea{width:100%;background:#06151b99;color:white;border:1px solid #e6c99b66;border-radius:12px;padding:13px;margin:8px 0;outline:none}button{border:0;border-radius:999px;background:#e8c894;color:#24372e;padding:13px 24px;font-weight:700;cursor:pointer;margin:8px 4px}button.secondary{background:#ffffff18;color:#fff0d5;border:1px solid #ffffff55}.options{display:grid;gap:9px}.options button{width:100%;margin:0;background:#ffffff14;color:#fff;border:1px solid #e6c99b44;text-align:left;border-radius:12px}.options button.selected{background:#e6c99b;color:#172a29}.muted{font-size:13px;opacity:.8}.ball{display:inline-flex;align-items:center;justify-content:center;border-radius:50%;width:36px;height:36px;margin:4px;background:radial-gradient(circle at 30% 25%,#fff9,#d23c59 45%,#59132e);box-shadow:0 0 12px #f8cb8766}.ball.gold{background:radial-gradient(circle at 30% 25%,#fff9,#dfb04b 45%,#8a4e14)}.ball.ice{background:radial-gradient(circle at 30% 25%,#fff9,#70b8ca 45%,#22546e)}.ball.rose{background:radial-gradient(circle at 30% 25%,#fff9,#e79ab3 45%,#914e6c)}.question{margin:20px 0;text-align:left}.question strong{display:block;margin:0 0 10px}.share{word-break:break-all;color:#ffe0a2}small{color:#b9c7c1}</style><main><div class="eyebrow">WINTER WISHES · 2026</div><div class="tree">🎄</div><h1>冬夜挂球</h1><p>把关于你的十个小秘密，藏进一棵圣诞树。<br>邀请朋友猜一猜，再挂上一颗属于他们的星光。</p><section class="panel" id="app">正在点亮圣诞树…</section><small>愿所有温柔的等待，都能被看见 ✦</small></main><script type="module">
const app=document.querySelector('#app'),path=location.pathname;
const bank=[['周末最想怎么过？','睡到自然醒','出门散步','和朋友吃饭'],['冬天更喜欢喝什么？','热可可','桂花拿铁','热奶茶'],['想收到什么圣诞礼物？','手写信','一本书','一顿好吃的'],['到了陌生城市，先去哪里？','当地市场','博物馆','公园'],['最喜欢一天中的哪个时刻？','清晨','午后','深夜'],['最想一起做哪件事？','看电影','去看雪','一起做饭'],['更喜欢哪种聊天方式？','当面聊','发消息','打电话'],['最想去哪里放假？','海边','山里','小城'],['心情不好时会做什么？','听歌','散步','吃甜品'],['最想把圣诞树放在哪里？','窗边','客厅','院子'],['如果突然多放一天假？','补觉','约朋友','到处逛'],['下雪时会先做什么？','拍照','踩雪','看雪'],['朋友在哪里找到我？','咖啡店','图书馆','家里'],['喜欢哪种圣诞歌？','经典','爵士','流行'],['更喜欢哪种聚会？','两三个人','热闹一群人','做手工'],['礼物包装选哪种？','牛皮纸','红缎带','涂鸦纸'],['想送朋友什么祝福？','睡个好觉','天天开心','早点见面'],['圣诞晚餐最爱哪道？','烤鸡','意面','蛋糕'],['临时约见面最管用的话？','去吃宵夜','去看电影','出来拍照'],['更想收到哪种惊喜？','小纸条','突然来访','神秘礼物']];
const rand=()=>bank.map((x,i)=>i).sort(()=>Math.random()-.5).slice(0,10);
let indices=rand(),answers=Array(10).fill(0),tree,attempt,step=0,score=0;
const escape=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const request=async(url,body)=>{const r=await fetch(url,body?{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)}:{});const j=await r.json();if(!r.ok)throw Error(j.error||'请求失败');return j};
function home(){app.innerHTML='<h2>点亮你的专属圣诞树</h2><p>20 道温暖的问题，随机选 10 道。好友猜完就能挂球。</p><button id="create">创建我的圣诞树</button>';document.querySelector('#create').onclick=create}
function create(){app.innerHTML='<h2>写给冬天的自己</h2><input id="name" maxlength="24" placeholder="你的昵称"><input id="mood" maxlength="36" placeholder="一句冬日寄语（可选）"><div id="questions"></div><button class="secondary" id="shuffle">换一组问题</button><button id="submit">生成圣诞树 ✦</button>';renderQs();document.querySelector('#shuffle').onclick=()=>{indices=rand();answers=Array(10).fill(0);renderQs()};document.querySelector('#submit').onclick=async()=>{try{const name=document.querySelector('#name').value,mood=document.querySelector('#mood').value;const questions=indices.map((n,i)=>({prompt:bank[n][0],options:bank[n].slice(1),answer:answers[i]}));const r=await request('/api/trees',{name,mood,questions});location.href='/tree/'+r.id}catch(e){alert(e.message)}}}
function renderQs(){document.querySelector('#questions').innerHTML=indices.map((n,i)=>'<div class="question"><strong>'+(i+1)+'. '+escape(bank[n][0])+'</strong><div class="options">'+bank[n].slice(1).map((o,j)=>'<button data-i="'+i+'" data-j="'+j+'" class="'+(answers[i]===j?'selected':'')+'">'+escape(o)+'</button>').join('')+'</div></div>').join('');document.querySelectorAll('[data-i]').forEach(b=>b.onclick=()=>{answers[+b.dataset.i]=+b.dataset.j;renderQs()})}
async function load(){try{tree=await request('/api/trees/'+path.split('/')[2]);welcome()}catch(e){app.textContent=e.message}}
function welcome(){app.innerHTML='<h2>'+escape(tree.name)+' 的圣诞树</h2><p>'+escape(tree.mood||'一颗挂满祝福的树')+'</p><div>'+tree.ornaments.map(o=>'<span class="ball '+escape(o.color)+'" title="'+escape(o.name)+' · '+o.score+'分">✦</span>').join('')+'</div><p>已有 '+tree.count+' 位朋友挂球</p><input id="guest" maxlength="24" placeholder="你的名字"><div id="colors" class="options">'+[['cranberry','莓果红'],['gold','星光金'],['ice','松霜蓝'],['rose','暖粉色']].map((x,i)=>'<button data-color="'+x[0]+'" class="'+(i===0?'selected':'')+'">'+x[1]+'</button>').join('')+'</div><button id="start">开始答题</button><p class="muted">分享链接：<span class="share">'+escape(location.href)+'</span></p><button class="secondary" id="copy">复制邀请链接</button><button class="secondary" id="new">我也要创建</button>';let color='cranberry';document.querySelectorAll('[data-color]').forEach(b=>b.onclick=()=>{color=b.dataset.color;document.querySelectorAll('[data-color]').forEach(x=>x.classList.toggle('selected',x===b))});document.querySelector('#start').onclick=async()=>{try{const r=await request('/api/trees/'+path.split('/')[2]+'/start',{name:document.querySelector('#guest').value,color});attempt=r.attempt;step=0;score=0;question()}catch(e){alert(e.message)}};document.querySelector('#copy').onclick=()=>navigator.clipboard.writeText(location.href).then(()=>alert('已复制'));document.querySelector('#new').onclick=()=>location.href='/create'}
function question(){const q=tree.questions[step];app.innerHTML='<p>第 '+(step+1)+' / 10 题</p><h2>'+escape(q.prompt)+'</h2><div class="options">'+q.options.map((o,i)=>'<button data-choice="'+i+'">'+escape(o)+'</button>').join('')+'</div>';document.querySelectorAll('[data-choice]').forEach(b=>b.onclick=async()=>{try{const r=await request('/api/trees/'+path.split('/')[2]+'/answer',{attempt,step,choice:+b.dataset.choice});score=r.score;step++;if(step===10){app.innerHTML='<h2>你的圣诞球已经挂好啦 ✨</h2><div class="tree">🎄</div><h2>'+score+' / 10 分</h2><p>你为这棵树带来了一点光。</p><button id="again">看看圣诞树</button>';document.querySelector('#again').onclick=()=>load()}else question()}catch(e){alert(e.message)}})}
if(path.startsWith('/tree/'))load();else if(path.startsWith('/create'))create();else home();
</script></html>`;
http.createServer(async(req,res)=>{try{const path=new URL(req.url,'http://localhost').pathname;if(path==='/health')return respond(res,{ok:true});if(path.startsWith('/api/'))return await api(req,res,path);if(/^\/$|^\/create\/?$|^\/tree\/[a-f0-9]{32}\/?$/.test(path)){res.writeHead(200,{'content-type':'text/html; charset=utf-8','X-Content-Type-Options':'nosniff'});return res.end(html)}err(res,'页面不存在',404)}catch(e){console.error(e);err(res,'暂时无法连接，请稍后再试',503)}}).listen(Number(process.env.PORT)||10000,'0.0.0.0',()=>console.log('Winter Wishes listening'));
