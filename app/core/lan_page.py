"""The page the phone opens (served by lan_share.py). One self-contained HTML file, no CDN.

Over HTTPS (after the phone trusts Study Hub's local certificate) it is an installable web app:
own icon, full screen, works as a target of Android's Share menu (manifest share_target + service worker).

Inside the Android app (window.StudyHubApp, a native bridge) the page hands over what a browser can't do
well: downloads, notifications, keeping the screen on; the app already trusts Study Hub's certificate.

Bottom bar: Send (note / task / file / link / clipboard + the laptop's clipboard history / from laptop) ·
Desktop (Study Hub desktops, open things on the laptop) · Files (shared folders) · Sound (laptop sound live
on the phone, phone music on the laptop) · Study (timer, today's tasks, notes) · Control (slides remote,
touchpad, keyboard, screen, show this phone on the laptop). Sections the laptop switched off are hidden; files / screen /
remote ask for the secure app when opened over http."""
from __future__ import annotations

import html
import json

_PAGE = r"""<!doctype html>
<html lang="__LANG__" dir="__DIR__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0d0e1a">
<meta name="referrer" content="no-referrer">
<title>Study Hub</title>
<link rel="manifest" href="/manifest.webmanifest">
<link rel="apple-touch-icon" href="/icon-192.png">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="Study Hub">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='16' fill='%237c5cff'/%3E%3Cpath d='M32 14 8 26l24 12 18-9v13h4V26z' fill='white'/%3E%3C/svg%3E">
<style>
:root{--bg:#0d0e1a;--card:#171829;--card2:#1e2036;--line:#2b2d48;--text:#eceefe;--dim:#9a9cc0;
--accent:#7c5cff;--accent2:#22d3ee;--ok:#34d399;--bad:#f87171;--warn:#fbbf24;--r:18px}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--bg);color:var(--text);
font:16px/1.5 "Segoe UI",system-ui,-apple-system,"Noto Sans Arabic",Tahoma,sans-serif}
body{min-height:100vh;padding:max(16px,env(safe-area-inset-top)) 16px 40px;
background:radial-gradient(900px 500px at 85% -10%,#2a1f6b55,transparent),radial-gradient(700px 400px at -10% 110%,#0e4f6355,transparent),var(--bg)}
.wrap{max-width:560px;margin:0 auto}
header{display:flex;align-items:center;gap:12px;margin:4px 0 16px}
.logo{width:44px;height:44px;border-radius:14px;background:linear-gradient(135deg,var(--accent),var(--accent2));
display:grid;place-items:center;box-shadow:0 8px 24px #7c5cff55;flex:none}
.logo svg{width:26px;height:26px}
h1{font-size:19px;margin:0;font-weight:650}
.sub{color:var(--dim);font-size:13px;display:flex;align-items:center;gap:6px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--dim);flex:none}
.dot.on{background:var(--ok);box-shadow:0 0 0 4px #34d39922}.dot.off{background:var(--bad)}
.lang{margin-inline-start:auto;background:var(--card);border:1px solid var(--line);color:var(--text);
border-radius:12px;padding:8px 12px;font:inherit;font-size:13px}
.tabs{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;background:var(--card);border:1px solid var(--line);
border-radius:var(--r);padding:6px;margin-bottom:14px}
.tab{position:relative;border:0;background:none;color:var(--dim);font:inherit;font-size:13px;padding:9px 4px;border-radius:13px;
display:flex;flex-direction:column;align-items:center;gap:3px;transition:.25s}
.tab svg{width:22px;height:22px}
.tab.active{background:var(--card2);color:var(--text);box-shadow:inset 0 0 0 1px var(--line)}
.tab.active svg{color:var(--accent2)}
.badge{position:absolute;top:4px;inset-inline-end:14px;min-width:18px;height:18px;border-radius:9px;background:var(--bad);
color:white;font-size:11px;font-weight:700;display:grid;place-items:center;padding:0 5px}
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:16px;animation:in .35s ease}
@keyframes in{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
label{display:block;font-size:13px;color:var(--dim);margin:2px 2px 6px}
input,textarea,select{width:100%;background:var(--bg);border:1px solid var(--line);color:var(--text);border-radius:14px;
padding:12px 14px;font:inherit;outline:none;transition:border .2s}
select{appearance:none}
input:focus,textarea:focus,select:focus{border-color:var(--accent)}
textarea{min-height:150px;resize:vertical}
.field{margin-bottom:12px}
.row{display:flex;gap:8px}.row>*{flex:1}
.ltr{direction:ltr;text-align:left}
.btn{width:100%;border:0;border-radius:14px;padding:14px;font:inherit;font-weight:600;color:white;
background:linear-gradient(135deg,var(--accent),#5b8cff);box-shadow:0 10px 26px #7c5cff44;transition:transform .15s,opacity .2s}
.btn:active{transform:scale(.98)}.btn:disabled{opacity:.5}
.ghost{background:var(--card2);box-shadow:none;border:1px solid var(--line);color:var(--text)}
.mt{margin-top:8px}
.chips{display:flex;gap:6px;flex-wrap:wrap}
.chip{border:1px solid var(--line);background:var(--bg);color:var(--dim);border-radius:20px;padding:7px 14px;font:inherit;font-size:14px}
.chip.on{border-color:var(--accent);color:var(--text);background:#7c5cff22}
.pick{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px}
.drop{border:2px dashed var(--line);border-radius:16px;padding:20px 8px;text-align:center;color:var(--dim);background:none;font:inherit}
.drop svg{width:28px;height:28px;color:var(--accent2);display:block;margin:0 auto 6px}
.drop b{color:var(--text);display:block;font-size:15px}
.files{list-style:none;margin:0 0 12px;padding:0;display:grid;gap:8px}
.files li,.item{background:var(--bg);border:1px solid var(--line);border-radius:12px;padding:10px 12px;font-size:14px}
.files .r{display:flex;justify-content:space-between;gap:8px;align-items:center}
.files img{width:40px;height:40px;object-fit:cover;border-radius:8px;flex:none}
.name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:ltr;flex:1}
.bar{height:5px;background:var(--line);border-radius:4px;margin-top:8px;overflow:hidden}
.bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--accent),var(--accent2));transition:width .15s}
.hint{color:var(--dim);font-size:12.5px;margin-top:10px}
.item{margin-bottom:8px;animation:in .3s ease}
.item.new{border-color:#7c5cff88}
.item .t{font-weight:600;margin-bottom:2px;overflow:hidden;text-overflow:ellipsis}
.item .c{color:var(--dim);font-size:13px;white-space:pre-wrap;word-break:break-word;max-height:7.5em;overflow:hidden}
.item .a{display:flex;gap:6px;margin-top:8px}
.item .a button{flex:1;padding:9px;border-radius:10px;font-size:13px}
.empty{color:var(--dim);text-align:center;padding:20px 0}
.toast{position:fixed;inset-inline:16px;bottom:max(20px,env(safe-area-inset-bottom));max-width:528px;margin:auto;visibility:hidden;
background:var(--card2);border:1px solid var(--line);border-radius:14px;padding:12px 16px;z-index:9;
transform:translateY(calc(100% + 60px));transition:transform .35s cubic-bezier(.2,.9,.3,1.2),visibility .35s;box-shadow:0 12px 30px #0008}
.toast.show{transform:none;visibility:visible}.toast.ok{border-color:#34d39966}.toast.bad{border-color:#f8717166}
.history{margin-top:16px}
.history h2{font-size:13px;color:var(--dim);font-weight:500;margin:0 4px 8px}
.history div{font-size:13.5px;padding:8px 12px;border-radius:12px;background:var(--card);border:1px solid var(--line);
margin-bottom:6px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.pair p{color:var(--dim);text-align:center}
.code{letter-spacing:3px;text-align:center;font-size:20px;text-transform:uppercase;direction:ltr}
.hidden{display:none!important}
.steps{margin:0;padding-inline-start:20px;color:var(--dim);font-size:14px}
.steps li{margin:10px 0}
.steps b{color:var(--text)}
.note{color:var(--dim);font-size:12.5px;margin-top:10px;border-inline-start:3px solid var(--warn);padding-inline-start:10px}
a.btn{display:block;text-align:center;text-decoration:none;margin:8px 0}
body{padding-bottom:calc(96px + env(safe-area-inset-bottom))}
.toast{bottom:calc(84px + env(safe-area-inset-bottom))}
.nav{position:fixed;inset-inline:0;bottom:0;display:flex;background:#111225e6;backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
border-top:1px solid var(--line);padding:6px 6px max(8px,env(safe-area-inset-bottom));z-index:8}
.nav button{flex:1;border:0;background:none;color:var(--dim);font:inherit;font-size:11.5px;display:flex;flex-direction:column;
align-items:center;gap:2px;padding:6px 0;border-radius:12px;transition:.2s}
.nav svg{width:24px;height:24px}.nav button.on{color:var(--text);background:#ffffff0a}.nav button.on svg{color:var(--accent2)}
.nav button.locked{opacity:.55}
.seg{display:flex;gap:6px;overflow-x:auto;margin-bottom:12px;scrollbar-width:none}.seg::-webkit-scrollbar{display:none}
.seg button{flex:none;border:1px solid var(--line);background:var(--card);color:var(--dim);border-radius:20px;padding:8px 16px;font:inherit;font-size:14px}
.seg button.on{border-color:var(--accent);color:var(--text);background:#7c5cff22}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(92px,1fr));gap:10px}
.tile{border:1px solid var(--line);background:var(--card);color:var(--text);border-radius:16px;padding:12px 6px 10px;font:inherit;font-size:12.5px;
display:flex;flex-direction:column;align-items:center;gap:8px;min-width:0;animation:in .3s ease}
.tile span{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ic{width:46px;height:46px;border-radius:13px;background:var(--card2);display:grid;place-items:center;font-size:22px;overflow:hidden}
.ic img{width:34px;height:34px;object-fit:contain}
.rows{display:grid;gap:6px}
.frow{display:flex;align-items:center;gap:12px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:10px 12px;
color:var(--text);font:inherit;text-align:start;width:100%}
.frow .fi{font-size:22px;flex:none;width:28px;text-align:center}.frow .fn{flex:1;min-width:0}
.frow .fn b{display:block;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.frow .fn small{color:var(--dim);font-size:12px}
.crumbs{display:flex;gap:4px;flex-wrap:wrap;align-items:center;margin-bottom:10px;font-size:13px;color:var(--dim)}
.crumbs button{border:0;background:var(--card2);color:var(--text);border-radius:10px;padding:5px 10px;font:inherit;font-size:13px}
.sheet{position:fixed;inset:0;background:#0009;z-index:10;display:flex;align-items:flex-end;opacity:0;transition:opacity .25s}
.sheet.show{opacity:1}.sheet>div{width:100%;max-width:560px;margin:0 auto;background:var(--card);border:1px solid var(--line);border-bottom:0;
border-radius:22px 22px 0 0;padding:18px 16px max(18px,env(safe-area-inset-bottom));transform:translateY(40px);transition:transform .25s;max-height:85vh;overflow:auto}
.sheet.show>div{transform:none}.sh-t{font-weight:650;margin-bottom:6px;overflow-wrap:anywhere}
.pre{white-space:pre-wrap;word-break:break-word;color:var(--dim);font-size:14px;max-height:40vh;overflow:auto;background:var(--bg);border-radius:12px;padding:10px}
.viewer{position:fixed;inset:0;background:#000e;z-index:11;display:grid;place-items:center}.viewer img{max-width:100%;max-height:100%}
.timer{position:relative;width:230px;height:230px;margin:6px auto 14px}.timer svg{width:100%;height:100%;transform:rotate(-90deg)}
.timer circle{fill:none;stroke-width:8}.timer .trk{stroke:var(--line)}.timer .prg{stroke:url(#tg);stroke-linecap:round;transition:stroke-dashoffset .9s linear}
.tm{position:absolute;inset:0;display:grid;place-content:center;text-align:center}.tm b{font-size:46px;font-weight:600;direction:ltr;letter-spacing:1px}
.tm span{color:var(--dim);font-size:14px}
.task{display:flex;gap:12px;align-items:flex-start;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px;transition:.35s}
.task.done{opacity:0;transform:translateX(30px)}.task .ck{width:24px;height:24px;border-radius:50%;border:2px solid var(--dim);flex:none;background:none;margin-top:1px}
.task .ck.on{background:var(--ok);border-color:var(--ok)}.task small{color:var(--dim);font-size:12px;display:block}
.pill{display:inline-block;width:8px;height:8px;border-radius:50%;margin-inline-end:6px}
.big{flex:1;height:150px;border:1px solid var(--line);border-radius:22px;background:var(--card2);color:var(--text);font-size:56px;font-weight:300}
.big:active,.k:active{background:#7c5cff33}
.g3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:10px}
.k{border:1px solid var(--line);background:var(--card);color:var(--text);border-radius:14px;padding:13px 4px;font:inherit;font-size:14px}
.pad{height:52vh;min-height:260px;border:1px solid var(--line);border-radius:22px;background:repeating-linear-gradient(45deg,#ffffff04 0 12px,transparent 12px 24px),var(--card);
display:grid;place-items:center;color:var(--dim);font-size:13px;touch-action:none;user-select:none;-webkit-user-select:none;text-align:center;padding:20px}
.screenbox{overflow:auto;border-radius:14px;border:1px solid var(--line);background:#000;max-height:70vh}
.screenbox img{display:block;width:100%;touch-action:manipulation;-webkit-touch-callout:none;user-select:none}.screenbox.z img{width:200%}
.screenbox.full{position:fixed;inset:0;z-index:20;max-height:none;border:0;border-radius:0;display:grid;place-items:center;overflow:hidden}
.screenbox.full img,.screenbox.full.z img{width:100vw;height:100vh;height:100dvh;object-fit:contain}
.fsx{position:fixed;top:max(10px,env(safe-area-inset-top));inset-inline-end:max(10px,env(safe-area-inset-right));z-index:21;width:42px;height:42px;
border-radius:50%;border:1px solid #ffffff33;background:#000a;color:#fff;font-size:18px;opacity:.55}
.fsx:active{opacity:1}
.fsx.r2{inset-inline-end:max(62px,calc(env(safe-area-inset-right) + 52px));font-size:15px}.fsx.on{background:#7c5cffcc;opacity:.9}
.lbl{color:var(--dim);font-size:13px;margin:14px 2px 6px}
.clip{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:10px 12px;display:flex;gap:10px;align-items:flex-start}
.clip .cx{flex:1;min-width:0;font-size:14px;white-space:pre-wrap;word-break:break-word;max-height:6.2em;overflow:hidden}
.clip small{display:block;color:var(--dim);font-size:11.5px;margin-top:3px}
.clip img{max-width:100%;max-height:140px;border-radius:8px;display:block}
.clip .cb{flex:none;border:1px solid var(--line);background:var(--card2);color:var(--text);border-radius:10px;padding:8px 10px;font:inherit;font-size:13px}
.listen{width:170px;height:170px;border-radius:50%;margin:10px auto 16px;display:grid;place-items:center;font-size:58px;color:white;border:0;
background:linear-gradient(135deg,var(--accent),var(--accent2));box-shadow:0 14px 40px #7c5cff55;transition:transform .2s}
.listen:active{transform:scale(.96)}.listen.on{animation:pulse 1.6s ease-in-out infinite}
@keyframes pulse{50%{box-shadow:0 0 0 18px #7c5cff22,0 14px 40px #7c5cff55}}
input[type=range]{padding:0;height:34px;accent-color:var(--accent);background:none;border:0}
.np{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:16px;margin-bottom:12px}
.np b{display:block;font-size:17px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.np .bar{height:6px;margin:12px 0 4px}.np .tt{display:flex;justify-content:space-between;color:var(--dim);font-size:12px;direction:ltr}
.q{display:flex;gap:10px;align-items:center;padding:10px 12px;border-radius:12px;background:var(--card);border:1px solid var(--line);font-size:14px}
.q.on{border-color:var(--accent);color:var(--text)}.q span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
audio{width:100%;margin-top:10px}
.handoff{display:grid;gap:8px;margin-bottom:12px}
.ho{display:flex;align-items:center;gap:10px;background:linear-gradient(135deg,#7c5cff22,#22d3ee18);border:1px solid #7c5cff55;
border-radius:16px;padding:10px 12px;animation:in .3s ease}
.ho .hx{flex:1;min-width:0}.ho b{display:block;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ho small{color:var(--dim);font-size:12px}.ho .go{flex:none;border:0;border-radius:12px;padding:9px 14px;font:inherit;font-size:13px;
font-weight:600;color:#fff;background:linear-gradient(135deg,var(--accent),#5b8cff)}
.ho .x{flex:none;border:0;background:none;color:var(--dim);font-size:18px;padding:4px 6px}
.pv{position:fixed;inset:0;z-index:15;background:#0b0c16;display:flex;flex-direction:column}
.pv header{margin:0;padding:max(10px,env(safe-area-inset-top)) 12px 10px;background:#111225;border-bottom:1px solid var(--line)}
.pv .pages{flex:1;overflow:auto;-webkit-overflow-scrolling:touch;display:grid;place-items:start center;padding:10px;touch-action:pan-y pinch-zoom}
.pv img{width:100%;max-width:1100px;background:#fff;border-radius:6px;box-shadow:0 8px 30px #0008}
.pv.z img{width:180%;max-width:none}
.pv .pbar{display:flex;gap:8px;padding:10px 12px max(12px,env(safe-area-inset-bottom));background:#111225;border-top:1px solid var(--line)}
.pv .pbar button{flex:1}.pv .pn{flex:none;min-width:90px;text-align:center;color:var(--dim);font-size:14px;align-self:center;direction:ltr}
.stand{position:fixed;inset:0;z-index:16;background:radial-gradient(900px 600px at 80% -10%,#2a1f6b66,transparent),#07080f;color:#fff;
display:grid;grid-template-columns:1.2fr 1fr;gap:18px;padding:max(18px,env(safe-area-inset-top)) 22px 18px;transition:box-shadow .3s}
.stand.alert{box-shadow:inset 0 0 0 4px #f87171,inset 0 0 60px #f8717155}
.stand .clk{font-size:min(19vw,150px);font-weight:250;line-height:1;letter-spacing:-2px;direction:ltr}
.stand .day{color:#9a9cc0;font-size:20px;margin-top:6px}
.stand .sx{position:absolute;top:max(10px,env(safe-area-inset-top));inset-inline-end:12px;width:42px;height:42px;border-radius:50%;
border:1px solid #ffffff22;background:#0008;color:#fff;font-size:18px;opacity:.6}
.stand .col{display:grid;gap:12px;align-content:start;min-width:0}
.stand .tile{background:#ffffff0d;border:1px solid #ffffff14;border-radius:18px;padding:12px 14px;min-width:0}
.stand .tile h3{margin:0 0 4px;font-size:12px;color:#9a9cc0;font-weight:500;letter-spacing:.3px}
.stand .tile b{font-size:18px;font-weight:600;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.stand .tile small{color:#9a9cc0}.stand .tl{margin:0;padding:0;list-style:none;font-size:15px}
.stand .tl li{padding:3px 0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.stand .tmr{display:flex;align-items:center;gap:14px;margin-top:22px}
.stand .tmr b{font-size:44px;font-weight:300;direction:ltr}.stand .tmr button{border:0;border-radius:50%;width:56px;height:56px;font-size:22px;
color:#fff;background:linear-gradient(135deg,var(--accent),var(--accent2))}
.stand .dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-inline-end:6px;vertical-align:middle}
@media (orientation:portrait){.stand{grid-template-columns:1fr;align-content:start}.stand .clk{font-size:24vw}}
.labbar{display:flex;align-items:center;gap:10px;margin-bottom:12px;padding:10px 12px;border-radius:16px;
background:linear-gradient(135deg,#12a59433,#22d3ee18);border:1px solid #12a59488;animation:in .3s ease}
.labbar .hx{flex:1;min-width:0}.labbar b{display:block;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.labbar small{color:var(--dim);font-size:12px}.labbar .go{flex:none;border:0;border-radius:12px;padding:10px 14px;font:inherit;
font-size:14px;font-weight:600;color:#fff;background:linear-gradient(135deg,#12a594,#0e7490)}
.hx-later{flex:none;border:0;background:none;color:var(--dim);font:inherit;font-size:12px;padding:6px}
.camrow{display:flex;gap:8px;margin:10px 0 2px}.camrow button{flex:1;border:1px solid var(--line);border-radius:12px;
background:var(--card);color:var(--text);font:inherit;font-size:13px;padding:10px 8px}
.cam .scanframe{position:absolute;left:8%;right:8%;top:14%;bottom:30%;border:2px dashed #ffffffaa;border-radius:10px;pointer-events:none}
.deckgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:6px}
.deckb{position:relative;aspect-ratio:1;border:0;border-radius:18px;color:#fff;font:inherit;display:flex;flex-direction:column;
align-items:center;justify-content:center;gap:6px;padding:6px;box-shadow:inset 0 0 0 1px #ffffff1a;transition:transform .08s}
.deckb:active{transform:scale(.94)}.deckb .e{font-size:30px;line-height:1}.deckb .l{font-size:12px;font-weight:600;
max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.deckb.on{box-shadow:inset 0 0 0 3px #fff}.deckb .lvl{position:absolute;left:10px;right:10px;bottom:8px;height:4px;
border-radius:2px;background:#ffffff33;overflow:hidden}.deckb .lvl i{display:block;height:100%;background:#fff}
.airbtn{width:100%;margin-top:10px;border:1px solid var(--line);border-radius:14px;background:var(--card);color:var(--text);
font:inherit;font-size:15px;padding:16px;touch-action:none;user-select:none}
.airbtn.on{border-color:var(--accent);background:#7c5cff33}
.labdot{width:10px;height:10px;border-radius:50%;background:#ef4444;flex:none;animation:blink 1.4s infinite}
@keyframes blink{50%{opacity:.25}}
.cam{position:fixed;inset:0;z-index:60;background:#000;display:flex;flex-direction:column}
.cam video{flex:1;min-height:0;width:100%;object-fit:cover;background:#000}
.cam .top{position:absolute;top:0;left:0;right:0;display:flex;align-items:center;gap:8px;padding:12px;
background:linear-gradient(#000a,#0000);color:#fff;font-size:14px}
.cam .top b{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cam .round{border:0;width:44px;height:44px;border-radius:50%;background:#ffffff26;color:#fff;font-size:20px;flex:none}
.cam .bottom{padding:10px 14px 18px;background:#000;display:grid;gap:10px}
.cam input{width:100%;border-radius:12px;border:1px solid #ffffff33;background:#ffffff14;color:#fff;padding:11px 12px;font:inherit;font-size:15px}
.cam .row{display:flex;align-items:center;justify-content:space-between;gap:12px}
.cam .shutter{width:72px;height:72px;border-radius:50%;border:4px solid #fff;background:#ffffffd9;flex:none}
.cam .shutter:active{transform:scale(.92);background:#fff}
.cam .shots{display:flex;gap:6px;overflow-x:auto;flex:1;min-width:0;direction:ltr}
.cam .shots i{position:relative;flex:none;width:46px;height:46px;border-radius:8px;background-size:cover;background-position:center;border:2px solid #ffffff55}
.cam .shots i.ok{border-color:#22c55e}.cam .shots i.bad{border-color:#ef4444}
.cam .shots i.wait::after{content:"";position:absolute;inset:0;background:#0008;border-radius:6px}
.cam .count{color:#fff;font-size:13px;min-width:70px;text-align:end}
.cam .flash{position:absolute;inset:0;background:#fff;opacity:0;pointer-events:none;transition:opacity .25s}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="logo"><svg viewBox="0 0 24 24" fill="white"><path d="M12 3 1 9l11 6 9-4.9V17h2V9z"/><path d="M5 13.2v4L12 21l7-3.8v-4L12 17z" opacity=".7"/></svg></div>
    <div><h1>Study Hub</h1><div class="sub"><span class="dot" id="dot"></span><span id="status"></span></div></div>
    <button class="lang hidden" id="standBtn" title="Stand">🕰</button>
    <button class="lang" id="installBtn" title="App">📲</button>
    <button class="lang" id="langBtn"></button>
  </header>

  <section id="installView" class="card hidden"></section>

  <section id="pairView" class="card pair hidden">
    <p data-t="pairText"></p>
    <div class="field"><label data-t="code"></label><input id="codeInput" class="code" maxlength="11" autocomplete="off" autocapitalize="characters" placeholder="XXXXX-XXXXX"></div>
    <div class="field"><label data-t="deviceName"></label><input id="nameInput" maxlength="60"></div>
    <button class="btn" id="pairBtn" data-t="connect"></button>
  </section>

  <main id="mainView" class="hidden">
   <div id="accessBar" class="labbar hidden">
     <div class="hx"><b data-t="accTitle"></b><small id="accText"></small></div>
     <button class="go" id="accBtn"></button>
     <button class="hx-later" id="accLater"></button>
   </div>
   <div id="syncBars"></div>
   <div id="labBar" class="labbar hidden">
     <span class="labdot"></span>
     <div class="hx"><b id="labTitle"></b><small data-t="labRunning"></small></div>
     <button class="go" id="labCamBtn" data-t="labPhoto"></button>
   </div>
   <input type="file" id="labCapture" accept="image/*" capture="environment" class="hidden">
   <div id="cam" class="cam hidden">
     <video id="camVideo" playsinline autoplay muted></video>
     <div class="flash" id="camFlash"></div>
     <div class="scanframe hidden" id="camFrame"></div>
     <div class="top"><button class="round" id="camClose" aria-label="close">✕</button><b id="camTitle"></b>
       <button class="round" id="camFlip" aria-label="flip">⟲</button></div>
     <div class="bottom">
       <input id="camCaption" maxlength="300" autocomplete="off">
       <div class="row"><div class="shots" id="camShots"></div><button class="shutter" id="camShutter" aria-label="shutter"></button>
         <span class="count" id="camCount"></span></div>
     </div>
   </div>
   <div class="handoff" id="handoff"></div>
   <div data-sec-pane="send">
    <nav class="tabs" id="tabs">
      <button class="tab active" data-tab="note"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h7M9 17h5"/></svg><span data-t="tNote"></span></button>
      <button class="tab" data-tab="task"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="4" y="4" width="16" height="16" rx="4"/><path d="m8.5 12 2.5 2.5 4.5-5"/></svg><span data-t="tTask"></span></button>
      <button class="tab" data-tab="file"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 15v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4"/></svg><span data-t="tFile"></span></button>
      <button class="tab" data-tab="link"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/></svg><span data-t="tLink"></span></button>
      <button class="tab" data-tab="clip"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="6" y="4" width="12" height="17" rx="2"/><path d="M9 4V3h6v1M9 10h6M9 14h6"/></svg><span data-t="tClip"></span></button>
      <button class="tab" data-tab="inbox"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="5" width="13" height="10" rx="2"/><path d="M1 19h17M19 8h2a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-2"/></svg><span data-t="tInbox"></span><span class="badge hidden" id="badge"></span></button>
    </nav>

    <section class="card" data-pane="note">
      <div class="field"><label data-t="title"></label><input id="noteTitle" maxlength="200"></div>
      <div class="field"><label data-t="noteText"></label><textarea id="noteText" maxlength="100000"></textarea></div>
      <div class="field subj"><label data-t="subject"></label><select data-subject></select></div>
      <button class="btn" data-send="note" data-t="sendNote"></button>
    </section>

    <section class="card hidden" data-pane="task">
      <div class="field"><label data-t="taskTitle"></label><input id="taskTitle" maxlength="300"></div>
      <div class="field"><label data-t="due"></label><div class="chips" id="due">
        <button class="chip on" data-v="today" data-t="today"></button><button class="chip" data-v="tomorrow" data-t="tomorrow"></button><button class="chip" data-v="none" data-t="noDate"></button></div></div>
      <div class="field"><label data-t="priority"></label><div class="chips" id="prio">
        <button class="chip" data-v="1" data-t="high"></button><button class="chip on" data-v="2" data-t="medium"></button><button class="chip" data-v="3" data-t="low"></button></div></div>
      <div class="field"><label data-t="details"></label><textarea id="taskDetails" maxlength="5000" style="min-height:80px"></textarea></div>
      <div class="field subj"><label data-t="subject"></label><select data-subject></select></div>
      <button class="btn" data-send="task" data-t="sendTask"></button>
    </section>

    <section class="card hidden" data-pane="file">
      <div class="pick">
        <button class="drop" id="camBtn"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/></svg><b data-t="camera"></b></button>
        <button class="drop" id="pickBtn"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 6h6l2 2h8v11H4z"/></svg><b data-t="pick"></b></button>
      </div>
      <div class="camrow"><button id="toClipPhoto"></button><button id="toClipScan"></button></div>
      <input id="camInput" type="file" accept="image/*" capture="environment" class="hidden">
      <input id="fileInput" type="file" multiple class="hidden">
      <ul class="files" id="fileList"></ul>
      <div class="field subj"><label data-t="fileSubject"></label><select data-subject></select></div>
      <button class="btn" id="sendFiles" disabled data-t="sendFiles"></button>
      <div class="hint" id="limit"></div>
    </section>

    <section class="card hidden" data-pane="link">
      <div class="field"><label data-t="url"></label><input id="linkUrl" class="ltr" type="url" inputmode="url" placeholder="https://"></div>
      <div class="field"><label data-t="titleOpt"></label><input id="linkTitle" maxlength="200"></div>
      <button class="btn" data-send="link" data-t="sendLink"></button>
      <div class="hint" data-t="linkHint"></div>
    </section>

    <section class="card hidden" data-pane="clip">
      <div class="field"><label data-t="clipText"></label><textarea id="clipText" maxlength="100000" style="min-height:120px"></textarea></div>
      <button class="btn" data-send="clip" data-t="sendClip"></button>
      <div class="hint" data-t="clipHint"></div>
      <div id="clipHist" class="hidden">
        <div class="lbl" data-t="clipHistory"></div>
        <div class="field"><input id="clipSearch" type="search" autocomplete="off"></div>
        <div class="rows" id="clipList"></div>
        <button class="btn ghost mt" id="clipMore" data-t="refresh"></button>
      </div>
      <div class="hint hidden" id="clipLocked" data-t="clipLocked"></div>
    </section>

    <section class="hidden" data-pane="inbox">
      <div id="inboxList"></div>
      <button class="btn ghost" id="refreshInbox" data-t="refresh"></button>
    </section>

    <section class="history" id="history"><h2 data-t="recent"></h2></section>
    <button class="btn ghost mt" id="forget" data-t="forget"></button>
   </div>

   <div data-sec-pane="desk" class="hidden">
     <div class="seg" id="deskChips"></div>
     <div class="grid" id="deskGrid"></div>
     <button class="btn ghost mt" id="deskRefresh" data-t="refresh"></button>
   </div>

   <div data-sec-pane="files" class="hidden">
     <div class="crumbs" id="fsCrumbs"></div>
     <div class="rows" id="fsList"></div>
     <button class="btn mt hidden" id="fsUpload" data-t="uploadHere"></button>
     <input id="fsInput" type="file" multiple class="hidden">
   </div>

   <div data-sec-pane="media" class="hidden">
     <div class="seg" data-seg="media"><button data-v="listen" data-t="mListen"></button><button data-v="music" data-t="mMusic"></button></div>
     <div data-seg-pane="media:listen">
       <button class="listen" id="listenBtn">▶</button>
       <div class="hint" id="listenState" style="text-align:center;margin-top:0"></div>
       <div class="lbl" data-t="phoneVolume"></div><input type="range" id="listenVol" min="0" max="150" value="100">
       <div class="hint" data-t="listenHint"></div>
     </div>
     <div data-seg-pane="media:music" class="hidden">
       <div class="np" id="npCard"><b id="npTitle"></b><div class="bar"><i id="npBar"></i></div>
         <div class="tt"><span id="npPos">0:00</span><span id="npDur">0:00</span></div>
         <div class="row mt" dir="ltr"><button class="k" id="npPrev">⏮</button><button class="k" id="npToggle">⏯</button><button class="k" id="npNext">⏭</button></div>
         <div class="lbl" data-t="laptopVolume"></div><input type="range" id="npVol" min="0" max="100" value="80"></div>
       <button class="btn" id="musicPick" data-t="musicPick"></button>
       <input id="musicInput" type="file" accept="audio/*,.mp3,.m4a,.flac,.ogg,.opus,.wav" multiple class="hidden">
       <div class="hint" id="musicUp"></div>
       <div class="rows mt" id="npQueue"></div>
       <button class="btn ghost mt" id="npClear" data-t="musicClear"></button>
     </div>
   </div>

   <div data-sec-pane="study" class="hidden">
     <div class="seg" data-seg="study"><button data-v="tasks" data-t="sTasks"></button><button data-v="notes" data-t="sNotes"></button></div>
     <div data-seg-pane="study:tasks" class="hidden"><div class="rows" id="taskList"></div>
       <button class="btn ghost mt" id="taskAdd" data-t="addTask"></button>
       <button class="btn ghost mt" id="notifyBtn"></button></div>
     <div data-seg-pane="study:notes" class="hidden">
       <div id="notesBrowse"><div class="field"><input id="noteSearch" type="search" autocomplete="off"></div><div class="rows" id="noteList"></div>
         <button class="btn ghost mt" id="noteNew" data-t="newNote"></button></div>
       <div id="noteEdit" class="hidden card">
         <div class="row" style="align-items:center;margin-bottom:10px"><button class="chip" id="noteBack" style="flex:none" data-t="back"></button><span class="hint" id="noteState" style="margin:0;text-align:end"></span></div>
         <div class="field"><input id="noteEditTitle" maxlength="300" dir="auto"></div>
         <div class="field"><textarea id="noteEditText" dir="auto" style="min-height:45vh"></textarea></div>
         <button class="btn" id="noteSave" data-t="save"></button>
       </div>
     </div>
   </div>

   <div data-sec-pane="ctrl" class="hidden">
     <div class="seg" data-seg="ctrl"><button data-v="deck" data-t="cDeck"></button><button data-v="slides" data-t="cSlides"></button><button data-v="pad" data-t="cPad"></button><button data-v="keys" data-t="cKeys"></button><button data-v="screen" data-t="cScreen"></button></div>
     <div data-seg-pane="ctrl:deck">
       <div class="deckgrid" id="deckGrid"></div>
       <div class="lbl" id="deckHint"></div>
     </div>
     <div data-seg-pane="ctrl:slides" class="hidden">
       <div class="row" dir="ltr"><button class="big" data-key="prev">‹</button><button class="big" data-key="next">›</button></div>
       <button class="airbtn" id="laserBtn"></button>
       <div class="g3"><button class="k" data-key="start_show" data-t="startShow"></button><button class="k" data-key="start_here" data-t="startHere"></button><button class="k" data-key="end_show" data-t="endShow"></button>
         <button class="k" data-key="black" data-t="black"></button><button class="k" data-key="white" data-t="white"></button><button class="k" data-key="fullscreen">F11</button></div>
       <div class="lbl" data-t="media"></div>
       <div class="g3" dir="ltr"><button class="k" data-key="vol_down">🔉</button><button class="k" data-key="mute">🔇</button><button class="k" data-key="vol_up">🔊</button>
         <button class="k" data-key="prev_track">⏮</button><button class="k" data-key="play">⏯</button><button class="k" data-key="next_track">⏭</button></div>
       <button class="btn ghost mt" id="mirrorBtn" data-t="mirrorMe"></button>
     </div>
     <div data-seg-pane="ctrl:pad" class="hidden">
       <div class="pad" id="pad"><span data-t="padHint"></span></div>
       <div class="row mt"><button class="k" id="lClick" data-t="lClick"></button><button class="k" id="rClick" data-t="rClick"></button></div>
       <button class="airbtn" id="airBtn"></button>
     </div>
     <div data-seg-pane="ctrl:keys" class="hidden">
       <div class="field"><textarea id="typeText" dir="auto" style="min-height:110px"></textarea></div>
       <button class="btn" id="typeSend" data-t="typeSend"></button>
       <div class="g3" dir="ltr"><button class="k" data-key="enter">Enter ⏎</button><button class="k" data-key="backspace">⌫</button><button class="k" data-key="tab">Tab</button>
         <button class="k" data-key="esc">Esc</button><button class="k" data-key="up">↑</button><button class="k" data-key="alt_tab">Alt+Tab</button>
         <button class="k" data-key="left">←</button><button class="k" data-key="down">↓</button><button class="k" data-key="right">→</button>
         <button class="k" data-key="space" data-t="space"></button><button class="k" data-key="show_desktop">Win+D</button><button class="k" data-key="close_tab">Ctrl+W</button></div>
     </div>
     <div data-seg-pane="ctrl:screen" class="hidden">
       <div class="row" style="margin-bottom:10px"><select id="monSel" class="hidden"></select><button class="chip on" id="liveBtn" style="flex:none" data-t="live"></button><button class="chip" id="zoomBtn" style="flex:none">2×</button><button class="chip" id="fullBtn" style="flex:none" data-t="fullScreen"></button></div>
       <div class="screenbox" id="screenBox"><img id="screenImg" alt="">
         <button class="fsx hidden" id="fsExit" aria-label="exit">✕</button><button class="fsx r2 hidden" id="fsRight" title="right click">R</button></div>
       <div class="hint" id="screenHint"></div>
     </div>
   </div>

   <section class="card hidden" data-sec-pane="locked">
     <h2 style="font-size:17px;margin:0 0 6px" data-t="lockedTitle"></h2><p class="hint" id="lockedText"></p><div id="lockedGo"></div>
   </section>

   <nav class="nav" id="nav">
     <button data-sec="send" class="on"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 12 20 4l-6 16-3-7z"/><path d="m11 13 9-9"/></svg><span data-t="secSend"></span></button>
     <button data-sec="desk"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg><span data-t="secDesk"></span></button>
     <button data-sec="files"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg><span data-t="secFiles"></span></button>
     <button data-sec="media"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/></svg><span data-t="secMedia"></span></button>
     <button data-sec="study"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l3 2M9 2h6"/></svg><span data-t="secStudy"></span></button>
     <button data-sec="ctrl"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="7" y="2" width="10" height="20" rx="5"/><path d="M12 6v4"/></svg><span data-t="secCtrl"></span></button>
   </nav>
  </main>
</div>
<textarea id="copyArea" style="position:fixed;top:-1000px;opacity:0" readonly></textarea>
<div class="toast" id="toast"></div>
<div class="sheet hidden" id="sheet"><div id="sheetBox"></div></div>
<div class="pv hidden" id="pdfView">
  <header><div style="flex:1;min-width:0"><h1 id="pvName" style="font-size:15px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"></h1>
    <div class="sub" id="pvSub"></div></div><button class="lang" id="pvZoom">2×</button><button class="lang" id="pvClose">✕</button></header>
  <div class="pages" id="pvPages"><img id="pvImg" alt=""></div>
  <div class="pbar" dir="ltr"><button class="btn ghost" id="pvPrev">‹</button><span class="pn" id="pvNum"></span>
    <button class="btn ghost" id="pvNext">›</button><button class="btn" id="pvLaptop" style="flex:1.6" data-t="toLaptopPdf"></button></div>
</div>
<div class="stand hidden" id="stand">
  <button class="sx" id="standClose">✕</button>
  <div class="col"><div class="clk" id="stClock">--:--</div><div class="day" id="stDay"></div>
    <div class="tmr"><div><b id="stTimer">--:--</b><div class="day" id="stPhase" style="font-size:15px"></div></div></div>
    <div class="tile" id="stLabT" style="margin-top:16px;display:none"><h3>🧪 LAB</h3><b id="stLab"></b></div></div>
  <div class="col">
    <div class="tile"><h3 data-t="stNext"></h3><b id="stClass">—</b><small id="stClassMeta"></small></div>
    <div class="tile"><h3 data-t="stTasks"></h3><ul class="tl" id="stTasks"></ul></div>
    <div class="tile" id="stSrvT"><h3 data-t="stServers"></h3><b id="stSrv">—</b><small id="stSrvDown"></small></div>
    <div class="tile"><h3 data-t="stLaptop"></h3><b id="stBat">—</b><small id="stMusic"></small></div>
  </div>
</div>
<script>
const INFO = __INFO__;
const T = {
 ar:{online:"متصل بـ ",offline:"مو متصل — تأكد إنك على نفس الشبكة",pairText:"على اللابتوب افتح Study Hub › الموبايل، وامسح رمز QR أو اكتب الكود هون.",
  code:"كود الربط",deviceName:"اسم هالجهاز",connect:"ربط",tNote:"ملاحظة",tTask:"مهمة",tFile:"ملف / صورة",tLink:"رابط",tClip:"حافظة",tInbox:"من اللابتوب",
  title:"العنوان (اختياري)",titleOpt:"الاسم (اختياري)",noteText:"الملاحظة",subject:"المادة (اختياري)",fileSubject:"احفظ بمجلد مادة (اختياري)",
  noSubject:"بدون مادة",sendNote:"إرسال الملاحظة",taskTitle:"شو المهمة؟",due:"الموعد",today:"اليوم",tomorrow:"بكرا",noDate:"بلا موعد",
  priority:"الأولوية",high:"عالية",medium:"متوسطة",low:"منخفضة",details:"تفاصيل (اختياري)",sendTask:"أضف المهمة",
  camera:"صوّر",pick:"اختار ملفات",limit:"حتى {n} ميغا للملف · بتنحفظ بمجلد الاستلام أو بمجلد المادة",sendFiles:"إرسال",
  url:"الرابط",sendLink:"تثبيت الرابط على سطح المكتب",linkHint:"بينضاف لسطح مكتب Study Hub مع شعار الموقع.",
  clipText:"نص لحافظة اللابتوب",sendClip:"انسخ للابتوب",clipHint:"بعدها اكبس Ctrl+V على اللابتوب.",
  inboxEmpty:"ما في شي من اللابتوب. من Study Hub اختار «ابعت للموبايل».",copy:"نسخ",open:"فتح",download:"تنزيل",refresh:"تحديث",
  recent:"آخر شي بعتّه",forget:"افصل هالجهاز",sent:"انبعت ✓",copied:"انسخ ✓",failed:"ما انبعت",badCode:"الكود غلط",
  revoked:"هالجهاز انفصل من اللابتوب — اربطه من جديد",closed:"الربط بجهاز جديد مسكّر من اللابتوب",tooBig:"أكبر من {n} ميغا",
  empty:"فاضي",blocked:"محاولات كتيرة — جرّب بعد شوي",lang:"English",newItems:"وصلك {n} من اللابتوب",
  instTitle:"ثبّت Study Hub كتطبيق",inst1:"نزّل شهادة Study Hub:",instCert:"تنزيل الشهادة",
  inst2:"بالموبايل: الإعدادات › الأمان والخصوصية › إعدادات أمان إضافية › التشفير وبيانات الاعتماد › تثبيت شهادة › شهادة CA، واختار StudyHub-CA.crt (مرة وحدة بس).",
  inst3:"افتح النسخة الآمنة:",instOpen:"افتح التطبيق الآمن",inst4:"من قائمة كروم ⋮ اختار «تثبيت التطبيق» أو «إضافة للشاشة الرئيسية».",
  instNote:"الشهادة بتشتغل بس لعناوين شبكتك المحلية (192.168… وأمثالها) وما بتقدر تُستعمل لأي موقع على الإنترنت. بتقدر تشيلها من نفس المكان بالإعدادات.",
  instPair:"اربط هالجهاز أول، بعدين ارجع لهون.",instNow:"ثبّت التطبيق",
  instHttps:"من قائمة كروم ⋮ اختار «تثبيت التطبيق». بعدها بيصير Study Hub بقائمة «مشاركة» بكل التطبيقات.",
  instDone:"التطبيق مثبّت ✓ — شارك أي صورة أو رابط أو ملف واختار Study Hub.",
  instIos:"آيفون: زر المشاركة › «إضافة إلى الشاشة الرئيسية».",instApk:"نزّل تطبيق أندرويد (APK)",
  instApkHint:"الأسهل: تطبيق أندرويد — ما بدو شهادة، بيلاقي اللابتوب لحاله، وبتوصلك الإشعارات بالخلفية. أو كمّل بالخطوات تحت لتثبيت صفحة الويب.",
  instApp:"إنت جوّا تطبيق Study Hub ✓", downloading:"عم ينزّل… شوف الإشعارات",shareSent:"انبعت {n} للابتوب ✓",
  secSend:"إرسال",secDesk:"سطح المكتب",secFiles:"الملفات",secStudy:"الدراسة",secCtrl:"تحكم",
  deskEmpty:"هالسطح فاضي.",openLaptop:"افتح عاللابتوب",openPhone:"افتح هون عالموبايل",openedLaptop:"انفتح عاللابتوب ✓",editNote:"عدّل الملاحظة",
  noShared:"ما في مجلدات مشتركة. من اللابتوب: Study Hub › الموبايل › الصلاحيات › شارك مجلد.",uploadHere:"ارفع لهون",preview:"عرض",
  folderEmpty:"المجلد فاضي",uploaded:"انرفع {n} ✓",
  sTimer:"المؤقت",sTasks:"مهام اليوم",sNotes:"الملاحظات",focus:"تركيز",short_break:"استراحة قصيرة",long_break:"استراحة طويلة",
  start:"ابدأ",pause:"وقّف",resume:"كمّل",reset:"من الأول",skip:"تخطّى",todayDone:"اليوم: {n}",
  notifyOn:"🔔 فعّل التنبيهات عالموبايل",notifyDone:"🔔 التنبيهات مفعّلة — بتوصل والتطبيق مفتوح أو بالخلفية",noNotify:"التنبيهات بدها التطبيق الآمن (📲)",
  tasksEmpty:"ما في مهام لليوم 🎉",taskDone:"خلصت ✓",addTask:"مهمة جديدة",
  notesSearch:"دوّر بالملاحظات…",notesEmpty:"ما في ملاحظات",newNote:"ملاحظة جديدة",back:"رجوع",save:"حفظ",saved:"انحفظ ✓",
  changed:"الملاحظة تغيّرت عاللابتوب — رجعت حمّلتها",lockedNote:"الملاحظة مقفولة بالخزنة",unsaved:"في تعديلات ما انحفظت",
  cSlides:"سلايدات",cPad:"تاتش باد",cKeys:"كيبورد",cScreen:"الشاشة",startShow:"▶ من الأول",startHere:"▶ من هون",endShow:"إنهاء",
  black:"شاشة سودا",white:"شاشة بيضا",media:"الصوت والميديا",padHint:"حرّك إصبع = ماوس · نقرة = كليك · إصبعين = سكرول · نقرة بإصبعين = كليك يمين",
  lClick:"كليك",rClick:"كليك يمين",typeSend:"اكتب عاللابتوب",space:"مسافة",live:"مباشر",
  screenHint:"اكبس على الصورة لتكبس بنفس المكان عاللابتوب · ضغطة مطوّلة = كليك يمين.",fullScreen:"⛶ ملء الشاشة",screenOnly:"عرض فقط — التحكم مسكّر من اللابتوب.",
  lockedTitle:"بدها التطبيق الآمن",lockedText:"هالقسم بيشتغل بس عبر HTTPS حتى ما حدا عالشبكة يقدر يتجسس. ثبّت الشهادة مرة وحدة (📲) وافتح التطبيق الآمن.",
  offCap:"هالميزة مسكّرة من اللابتوب (الموبايل › الصلاحيات).",evOutbox:"📥 وصلك من اللابتوب",
  secMedia:"الصوت",clipHistory:"سجل حافظة اللابتوب",clipSearch:"دوّر بالحافظة…",clipLocked:"🔒 سجل حافظة اللابتوب بيطلع بالتطبيق الآمن (📲) إذا مشغّل من اللابتوب.",
  toPhone:"نسخ هون",toLaptop:"لحافظة اللابتوب",onLaptopClip:"صار بحافظة اللابتوب ✓",pin:"تثبيت",unpin:"إلغاء التثبيت",del:"حذف",clipsEmpty:"الحافظة فاضية",
  clipOff:"سجل الحافظة متوقف عاللابتوب (الإعدادات › الأدوات).",
  mListen:"صوت اللابتوب",mMusic:"شغّل عاللابتوب",phoneVolume:"صوت الموبايل",laptopVolume:"صوت اللابتوب",
  listenHint:"بتسمع كل شي عم يطلع من سماعات اللابتوب، مباشرة. خلي التطبيق مفتوح.",listening:"عم تسمع اللابتوب 🎧",listenOff:"اكبس لتسمع",
  listenFail:"ما قدر يلتقط صوت اللابتوب",musicPick:"🎵 اختار موسيقى من الموبايل",musicClear:"فضّي القائمة",nothingPlaying:"ما في شي عم يشتغل",
  uploading:"عم يرفع {n}…",playHere:"▶ شغّل هون",mirrorMe:"📱 اعرض شاشة هالموبايل عاللابتوب",mirrorNo:"ثبّت scrcpy عاللابتوب أول: الموبايل › شاشة الموبايل",
  mirrorOk:"عم تنفتح شاشة الموبايل عاللابتوب",
  labRunning:"مختبر شغّال — الصور بتروح للتقرير", labPhoto:"📷 صوّر", camCaption:"تعليق قصير (اختياري)…",
  camSent:"{n} انبعتوا للتقرير", camNoCam:"ما قدرت افتح الكاميرا — رح افتح كاميرا الموبايل", camFail:"ما انبعتت الصورة، جرّب كمان مرة",
  labEnded:"المختبر خلص",
  cDeck:"الأزرار", deckHint:"رتّب الأزرار من اللابتوب: الموبايل › الأزرار.", deckEmpty:"ما في أزرار لسا — ضيفهم من اللابتوب: الموبايل › الأزرار.",
  deckSleep:"تنيّم اللابتوب؟", airHold:"🎯 اكبس وطوّل وحرّك الموبايل لتحرّك الماوس", laserHold:"🔴 ليزر: اكبس وطوّل وأشّر بالموبايل",
  airNo:"هالموبايل ما فيه حسّاس حركة", deckFail:"ما زبط عاللابتوب",
  toClipPhoto:"📷 صورة لحافظة اللابتوب", toClipScan:"📄 امسح ورقة للابتوب",
  camPhotoTitle:"صورة لحافظة اللابتوب", camScanTitle:"امسح ورقة — خليها كلها جوّا الإطار",
  camPhotoSent:"صارت الصورة بحافظة اللابتوب ✓", camScanSent:"انبعتت — كمّل المسح عاللابتوب ✓",
  accTitle:"الموبايل على اللابتوب", accNotif:"اسمح بالإشعارات لتشوفها وترد عليها من اللابتوب.", accDnd:"اسمح بعدم الإزعاج لتتحكم فيه وبالصامت من اللابتوب.",
  accAllow:"اسمح", syncMedia:"اسمح بالصور والفيديوهات ليشتغل نسخ الصور عاللابتوب.", syncTitle:"نسخ ومزامنة",
  syncPick:"اختار مجلد الموبايل لـ «{n}»", syncPickBtn:"اختار", syncPickHint:"المجلد يلي اخترته رح يضل نفس مجلد اللابتوب.", accType:"شغّل «الكتابة من اللابتوب» لتكتب بخانات الموبايل من كيبورد اللابتوب.", accLater:"بعدين",
  hoPdf:"كمّل القراءة", hoNote:"كمّل الملاحظة", hoPage:"صفحة {n}", openHere:"افتح هون", toLaptopPdf:"💻 كمّل عاللابتوب",
  sentLaptop:"صار جاهز عاللابتوب — اكبس 📱 بالشريط العلوي", hoSecure:"صفحات الـ PDF بتنعرض بس بالتطبيق الآمن (📲)",
  stNext:"المحاضرة الجاية", stTasks:"مهام اليوم", stServers:"السيرفرات", stLaptop:"اللابتوب", stNoClass:"ما في محاضرات اليوم",
  stAllUp:"كلها شغّالة", stDown:"{n} واقع", stCharging:"عم يشحن", stNoTasks:"ما في مهام 🎉"},
 en:{online:"Connected to ",offline:"Not connected — make sure you're on the same Wi-Fi",pairText:"On the laptop open Study Hub › Phone, then scan the QR code or type the code here.",
  code:"Pairing code",deviceName:"Name of this device",connect:"Pair",tNote:"Note",tTask:"Task",tFile:"File / photo",tLink:"Link",tClip:"Clipboard",tInbox:"From laptop",
  title:"Title (optional)",titleOpt:"Name (optional)",noteText:"Note",subject:"Subject (optional)",fileSubject:"Save in a subject folder (optional)",
  noSubject:"No subject",sendNote:"Send note",taskTitle:"What's the task?",due:"Due",today:"Today",tomorrow:"Tomorrow",noDate:"No date",
  priority:"Priority",high:"High",medium:"Medium",low:"Low",details:"Details (optional)",sendTask:"Add task",
  camera:"Take photo",pick:"Choose files",limit:"Up to {n} MB per file · saved in the receive folder or the subject folder",sendFiles:"Send",
  url:"Link",sendLink:"Pin link to the desktop",linkHint:"It's added to the Study Hub desktop with the site's logo.",
  clipText:"Text for the laptop clipboard",sendClip:"Copy to laptop",clipHint:"Then press Ctrl+V on the laptop.",
  inboxEmpty:"Nothing from the laptop yet. In Study Hub choose “Send to phone”.",copy:"Copy",open:"Open",download:"Download",refresh:"Refresh",
  recent:"Recently sent",forget:"Unpair this device",sent:"Sent ✓",copied:"Copied ✓",failed:"Not sent",badCode:"Wrong code",
  revoked:"This device was unpaired on the laptop — pair it again",closed:"Pairing new devices is closed on the laptop",tooBig:"Bigger than {n} MB",
  empty:"Nothing to send",blocked:"Too many tries — wait a few minutes",lang:"عربي",newItems:"{n} new from the laptop",
  instTitle:"Install Study Hub as an app",inst1:"Download Study Hub's certificate:",instCert:"Download certificate",
  inst2:"On the phone: Settings › Security & privacy › More security settings › Encryption & credentials › Install a certificate › CA certificate, then pick StudyHub-CA.crt (only once).",
  inst3:"Open the secure version:",instOpen:"Open the secure app",inst4:"In Chrome's ⋮ menu choose “Install app” or “Add to Home screen”.",
  instNote:"The certificate only works for your local network addresses (192.168… and similar) and cannot be used for any internet site. You can remove it in the same settings screen.",
  instPair:"Pair this device first, then come back here.",instNow:"Install the app",
  instHttps:"In Chrome's ⋮ menu choose “Install app”. Study Hub then appears in the Share menu of every app.",
  instDone:"App installed ✓ — share any photo, link or file and pick Study Hub.",
  instIos:"iPhone: Share button › “Add to Home Screen”.",instApk:"Download the Android app (APK)",
  instApkHint:"Easiest: the Android app — no certificate, finds the laptop by itself, notifications in the background. Or follow the steps below to install the web page.",
  instApp:"You're in the Study Hub app ✓", downloading:"Downloading… see the notification",shareSent:"Sent {n} to the laptop ✓",
  secSend:"Send",secDesk:"Desktop",secFiles:"Files",secStudy:"Study",secCtrl:"Control",
  deskEmpty:"This desktop is empty.",openLaptop:"Open on the laptop",openPhone:"Open here on the phone",openedLaptop:"Opened on the laptop ✓",editNote:"Edit the note",
  noShared:"No shared folders. On the laptop: Study Hub › Phone › Access › Share a folder.",uploadHere:"Upload here",preview:"View",
  folderEmpty:"Empty folder",uploaded:"Uploaded {n} ✓",
  sTimer:"Timer",sTasks:"Today's tasks",sNotes:"Notes",focus:"Focus",short_break:"Short break",long_break:"Long break",
  start:"Start",pause:"Pause",resume:"Resume",reset:"Restart",skip:"Skip",todayDone:"Today: {n}",
  notifyOn:"🔔 Turn on phone notifications",notifyDone:"🔔 Notifications on — they arrive while the app is open or in the background",noNotify:"Notifications need the secure app (📲)",
  tasksEmpty:"No tasks for today 🎉",taskDone:"Done ✓",addTask:"New task",
  notesSearch:"Search notes…",notesEmpty:"No notes",newNote:"New note",back:"Back",save:"Save",saved:"Saved ✓",
  changed:"The note changed on the laptop — reloaded it",lockedNote:"This note is locked in the vault",unsaved:"Unsaved changes",
  cSlides:"Slides",cPad:"Touchpad",cKeys:"Keyboard",cScreen:"Screen",startShow:"▶ From start",startHere:"▶ From here",endShow:"End",
  black:"Black screen",white:"White screen",media:"Volume & media",padHint:"Move a finger = mouse · tap = click · two fingers = scroll · two-finger tap = right click",
  lClick:"Click",rClick:"Right click",typeSend:"Type on the laptop",space:"Space",live:"Live",
  screenHint:"Tap the picture to click the same spot on the laptop · long press = right click.",fullScreen:"⛶ Full screen",screenOnly:"View only — control is switched off on the laptop.",
  lockedTitle:"Needs the secure app",lockedText:"This section only works over HTTPS so nobody on the network can snoop. Install the certificate once (📲) and open the secure app.",
  offCap:"This is switched off on the laptop (Phone › Access).",evOutbox:"📥 From the laptop",
  secMedia:"Sound",clipHistory:"Laptop clipboard history",clipSearch:"Search the clipboard…",clipLocked:"🔒 The laptop's clipboard history shows in the secure app (📲) when it's switched on on the laptop.",
  toPhone:"Copy here",toLaptop:"To laptop clipboard",onLaptopClip:"On the laptop clipboard ✓",pin:"Pin",unpin:"Unpin",del:"Delete",clipsEmpty:"The clipboard history is empty",
  clipOff:"Clipboard history is paused on the laptop (Settings › Tools).",
  mListen:"Laptop sound",mMusic:"Play on laptop",phoneVolume:"Phone volume",laptopVolume:"Laptop volume",
  listenHint:"You hear everything coming out of the laptop's speakers, live. Keep the app open.",listening:"Listening to the laptop 🎧",listenOff:"Tap to listen",
  listenFail:"Couldn't capture the laptop's sound",musicPick:"🎵 Pick music from the phone",musicClear:"Clear the queue",nothingPlaying:"Nothing playing",
  uploading:"Uploading {n}…",playHere:"▶ Play here",mirrorMe:"📱 Show this phone on the laptop",mirrorNo:"Install scrcpy on the laptop first: Phone › Phone screen",
  mirrorOk:"The phone screen is opening on the laptop",
  labRunning:"Lab running — photos go into the report", labPhoto:"📷 Photo", camCaption:"Short caption (optional)…",
  camSent:"{n} sent to the report", camNoCam:"Couldn't open the camera — using the phone's camera app", camFail:"The photo didn't send, try again",
  labEnded:"The lab ended",
  cDeck:"Deck", deckHint:"Set the buttons up on the laptop: Phone › Deck.", deckEmpty:"No buttons yet: add them on the laptop, Phone › Deck.",
  deckSleep:"Put the laptop to sleep?", airHold:"🎯 Hold and move the phone to move the pointer", laserHold:"🔴 Laser: hold and point the phone",
  airNo:"This phone has no motion sensor", deckFail:"That didn't work on the laptop",
  toClipPhoto:"📷 Photo to laptop clipboard", toClipScan:"📄 Scan a page to the laptop",
  camPhotoTitle:"Photo to the laptop clipboard", camScanTitle:"Scan a page: keep all of it inside the frame",
  camPhotoSent:"The photo is on the laptop's clipboard ✓", camScanSent:"Sent: finish the scan on the laptop ✓",
  accTitle:"Your phone on the laptop", accNotif:"Allow notification access to see and answer them on the laptop.", accDnd:"Allow Do Not Disturb access to control it (and silent mode) from the laptop.",
  accAllow:"Allow", syncMedia:"Allow photos and videos so photo backup to the laptop can run.", syncTitle:"Backup & sync",
  syncPick:"Choose the phone folder for “{n}”", syncPickBtn:"Choose", syncPickHint:"The folder you pick stays the same as the laptop's.", accType:"Turn on “Type from laptop” to type into the phone's text boxes with the laptop keyboard.", accLater:"Later",
  hoPdf:"Continue reading", hoNote:"Continue the note", hoPage:"page {n}", openHere:"Open here", toLaptopPdf:"💻 Continue on laptop",
  sentLaptop:"Ready on the laptop — click 📱 in its top bar", hoSecure:"PDF pages show only in the secure app (📲)",
  stNext:"Next class", stTasks:"Today's tasks", stServers:"Servers", stLaptop:"Laptop", stNoClass:"No more classes today",
  stAllUp:"all up", stDown:"{n} down", stCharging:"charging", stNoTasks:"No tasks 🎉"}
};
const $ = s => document.querySelector(s), $$ = s => document.querySelectorAll(s);
const APP = window.StudyHubApp || null;   // the Android app's native side (null in a browser)
function appSession(){ try { if (APP && token) APP.setSession(token); } catch(e){} }
const store = {get:k => { try { return localStorage.getItem(k); } catch(e){ return null; } },
               set:(k,v) => { try { localStorage.setItem(k, v); } catch(e){} },
               del:k => { try { localStorage.removeItem(k); } catch(e){} }};
let lang = store.get("sh_lang") || INFO.lang || "ar";
let token = store.get("sh_token") || "";
let subjects = [], lastPending = 0, inboxCache = [];
const t = (k, n) => (T[lang][k] || k).replace("{n}", n ?? "");

function applyLang(){
  document.documentElement.lang = lang; document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
  $$("[data-t]").forEach(el => el.textContent = t(el.dataset.t));
  const ns = document.getElementById("noteSearch"); if (ns) ns.placeholder = t("notesSearch");
  const cs = document.getElementById("clipSearch"); if (cs) cs.placeholder = t("clipSearch");
  $("#langBtn").textContent = t("lang"); $("#limit").textContent = t("limit", INFO.max_mb);
  $("#toClipPhoto").textContent = t("toClipPhoto"); $("#toClipScan").textContent = t("toClipScan");
  $("#airBtn").textContent = t("airHold"); $("#laserBtn").textContent = t("laserHold");
  fillSubjects(); renderHistory(); renderInbox(); setStatus(window._online);
}
function toast(msg, kind){
  const el = $("#toast"); el.textContent = msg; el.className = "toast show " + (kind||"");
  clearTimeout(el._t); el._t = setTimeout(() => el.className = "toast", 2600);
}
function setStatus(on){
  window._online = on; $("#dot").className = "dot " + (on === undefined ? "" : on ? "on" : "off");
  $("#status").textContent = on ? t("online") + INFO.name : on === false ? t("offline") : "…";
}
function guessName(){
  const ua = navigator.userAgent; let m;
  if (/iPhone/.test(ua)) return "iPhone"; if (/iPad/.test(ua)) return "iPad";
  if ((m = ua.match(/Android[^;]*;\s*([^;)]+?)(?:\s+Build|\))/))) return m[1].trim().slice(0, 40);
  if (/Android/.test(ua)) return "Android"; return "Phone";
}

async function api(path, opts){
  opts = opts || {}; opts.headers = Object.assign({"X-Device": token}, opts.headers || {});
  const r = await fetch(path, opts); let body = {};
  try { body = await r.json(); } catch(e) {}
  if (r.status === 401 && path !== "/api/pair"){ store.del("sh_token"); token = ""; showPair(t("revoked")); }
  return {status: r.status, body};
}
async function ping(){
  try {
    const r = await api("/api/ping?since=" + lastEv);
    if (r.status !== 200){ setStatus(false); return false; }
    setStatus(true); applyCaps(r.body.caps); onEvents(r.body.events || []); renderHandoff(r.body.handoff || {}); renderLab(r.body.lab || {});
    if (!$("[data-pane=clip]").classList.contains("hidden") && section === "send" && $("#clipHist").classList.contains("hidden") === !!caps.clipboard) loadClips();
    const n = r.body.pending || 0;
    $("#badge").textContent = n; $("#badge").classList.toggle("hidden", !n);
    if (n > lastPending){ toast(t("newItems", n - lastPending), "ok"); if (!$("[data-pane=inbox]").classList.contains("hidden")) loadInbox(); }
    lastPending = n; return true;
  } catch(e){ setStatus(false); return false; }
}
function showPair(msg){
  $("#mainView").classList.add("hidden"); $("#pairView").classList.remove("hidden");
  if (!$("#nameInput").value) $("#nameInput").value = store.get("sh_name") || guessName();
  if (msg) toast(msg, "bad");
}
async function showMain(){
  $("#pairView").classList.add("hidden"); $("#mainView").classList.remove("hidden");
  const r = await api("/api/subjects"); if (r.status === 200){ subjects = r.body; fillSubjects(); }
}
async function pair(code){
  code = (code||"").toUpperCase().replace(/[^A-Z0-9]/g, "");
  if (code.length < 10){ toast(t("badCode"), "bad"); return false; }
  const name = $("#nameInput").value.trim() || guessName(); store.set("sh_name", name);
  try {
    const r = await api("/api/pair", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({code, name})});
    if (r.status === 200){ token = r.body.token; store.set("sh_token", token); appSession(); setStatus(true); await showMain(); ping(); return true; }
    toast(r.status === 403 ? t("closed") : r.status === 429 ? t("blocked") : t("badCode"), "bad");
  } catch(e){ setStatus(false); toast(t("failed"), "bad"); }
  return false;
}

function fillSubjects(){
  $$("[data-subject]").forEach(sel => {
    const keep = sel.value; sel.innerHTML = "";
    const none = document.createElement("option"); none.value = ""; none.textContent = t("noSubject"); sel.append(none);
    subjects.forEach(s => { const o = document.createElement("option"); o.value = s.id; o.textContent = s.name; sel.append(o); });
    sel.value = keep; sel.closest(".subj").classList.toggle("hidden", !subjects.length);
  });
}
const paneSubject = pane => { const v = $(`[data-pane="${pane}"] [data-subject]`).value; return v ? Number(v) : null; };

// ---------------------------------------------------------------- history (this session)
function hist(){ try { return JSON.parse(sessionStorage.getItem("sh_hist") || "[]"); } catch(e){ return []; } }
function remember(kind, text){
  const h = [{kind, text: String(text).slice(0, 80)}, ...hist()].slice(0, 6);
  try { sessionStorage.setItem("sh_hist", JSON.stringify(h)); } catch(e){}
  renderHistory();
}
function renderHistory(){
  const box = $("#history"), h = hist();
  box.querySelectorAll("div").forEach(d => d.remove());
  box.classList.toggle("hidden", !h.length);
  const icon = {note:"📝", link:"🔗", file:"📎", clip:"📋", task:"✅"};
  h.forEach(x => { const d = document.createElement("div"); d.textContent = (icon[x.kind]||"") + " " + x.text; box.appendChild(d); });
}

// ---------------------------------------------------------------- sending text
function chipValue(id){ return $(`#${id} .chip.on`).dataset.v; }
$$(".chips").forEach(group => group.querySelectorAll(".chip").forEach(c => c.onclick = () => {
  group.querySelectorAll(".chip").forEach(x => x.classList.toggle("on", x === c));
}));
const FORMS = {
  note: () => ({body:{text:$("#noteText").value.trim(), title:$("#noteTitle").value.trim(), subject_id:paneSubject("note")},
                main:"text", clear:["#noteText","#noteTitle"]}),
  task: () => ({body:{title:$("#taskTitle").value.trim(), due:chipValue("due"), priority:Number(chipValue("prio")),
                details:$("#taskDetails").value.trim(), subject_id:paneSubject("task")}, main:"title", clear:["#taskTitle","#taskDetails"]}),
  link: () => ({body:{url:$("#linkUrl").value.trim(), title:$("#linkTitle").value.trim()}, main:"url", clear:["#linkUrl","#linkTitle"]}),
  clip: () => ({body:{text:$("#clipText").value}, main:"text", clear:["#clipText"]}),
};
async function sendJson(kind){
  const f = FORMS[kind]();
  if (!String(f.body[f.main]).trim()){ toast(t("empty"), "bad"); return; }
  const btn = document.querySelector(`[data-send="${kind}"]`); btn.disabled = true;
  try {
    const r = await api("/api/" + kind, {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(f.body)});
    if (r.status === 200){ toast(t("sent"), "ok"); remember(kind, f.body.title || f.body[f.main]); f.clear.forEach(s => $(s).value = ""); }
    else if (r.status === 403) toast(t("blocked"), "bad");
    else if (r.status !== 401) toast(t("failed"), "bad");
  } catch(e){ toast(t("failed"), "bad"); setStatus(false); }
  btn.disabled = false;
}

// ---------------------------------------------------------------- files
let picked = [];
function fmt(b){ return b > 1048576 ? (b/1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b/1024)) + " KB"; }
function addFiles(list){ picked = picked.filter(f => !f.done).concat([...list].map(file => ({file}))); renderFiles(); }
function renderFiles(){
  const ul = $("#fileList"); ul.innerHTML = "";
  picked.forEach(f => {
    const li = document.createElement("li"), row = document.createElement("div"), name = document.createElement("span"), size = document.createElement("span");
    row.className = "r"; name.className = "name"; name.textContent = f.file.name; size.textContent = f.state || fmt(f.file.size);
    if (f.file.type.startsWith("image/")){
      const img = document.createElement("img"); f.thumb = f.thumb || URL.createObjectURL(f.file); img.src = f.thumb; row.append(img);
    }
    row.append(name, size); li.append(row); const bar = document.createElement("div"); bar.className = "bar";
    const fill = document.createElement("i"); fill.style.width = (f.progress||0) + "%"; bar.append(fill); li.append(bar);
    f.el = {size, fill}; ul.append(li);
  });
  $("#sendFiles").disabled = !picked.some(f => !f.done);
}
function upload(f, subject, extra){
  return new Promise(resolve => {
    const x = new XMLHttpRequest(); x.open("POST", "/api/file");
    x.setRequestHeader("X-Device", token); x.setRequestHeader("X-Filename", encodeURIComponent(f.file.name || "photo.jpg"));
    if (subject) x.setRequestHeader("X-Subject", String(subject));
    Object.entries(extra || {}).forEach(([k, v]) => x.setRequestHeader(k, v));
    x.setRequestHeader("Content-Type", "application/octet-stream");
    x.upload.onprogress = e => { if (e.lengthComputable){ f.progress = e.loaded / e.total * 100; f.el.fill.style.width = f.progress + "%"; } };
    x.onload = () => resolve(x.status); x.onerror = () => resolve(0); x.send(f.file);
  });
}
async function sendFiles(){
  $("#sendFiles").disabled = true; let ok = 0; const subject = paneSubject("file");
  for (const f of picked.filter(f => !f.done)){
    if (f.file.size > INFO.max_mb * 1048576){ f.state = t("tooBig", INFO.max_mb); f.el.size.textContent = f.state; continue; }
    const s = await upload(f, subject);
    if (s === 200){ f.done = true; f.state = t("sent"); ok++; remember("file", f.file.name); }
    else if (s === 401){ store.del("sh_token"); token = ""; showPair(t("revoked")); break; }
    else f.state = s === 413 ? t("tooBig", INFO.max_mb) : t("failed");
    f.el.size.textContent = f.state;
  }
  if (ok) toast(t("sent"), "ok");
  $("#sendFiles").disabled = !picked.some(f => !f.done);
}

// ---------------------------------------------------------------- from the laptop
function copyText(text){
  const a = $("#copyArea"); a.value = text; a.select(); a.setSelectionRange(0, text.length);
  let ok = false; try { ok = document.execCommand("copy"); } catch(e){}
  if (!ok && navigator.clipboard) navigator.clipboard.writeText(text).then(() => toast(t("copied"), "ok"));
  else toast(ok ? t("copied") : t("failed"), ok ? "ok" : "bad");
}
async function got(id){ try { await api("/api/got/" + id, {method:"POST"}); } catch(e){} }
async function fetchFile(path, name){
  if (APP){ try { APP.download(path, name || "file"); toast(t("downloading"), "ok"); return true; } catch(e){} }
  try {
    const r = await fetch(path, {headers:{"X-Device": token}});
    if (!r.ok){ toast(t("failed"), "bad"); return false; }
    const url = URL.createObjectURL(await r.blob()), a = document.createElement("a");
    a.href = url; a.download = name || "file"; document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000); return true;
  } catch(err){ toast(t("failed"), "bad"); return false; }
}
async function download(e){ if (await fetchFile("/api/outbox/" + e.id, e.title)){ e.got = true; renderInbox(); } }
async function loadInbox(){
  const r = await api("/api/outbox"); if (r.status === 200){ inboxCache = r.body; renderInbox(); }
}
function renderInbox(){
  const box = $("#inboxList"); if (!box) return; box.innerHTML = "";
  if (!inboxCache.length){ const d = document.createElement("div"); d.className = "card empty"; d.textContent = t("inboxEmpty"); box.append(d); return; }
  inboxCache.forEach(e => {
    const d = document.createElement("div"); d.className = "item" + (e.got ? "" : " new");
    const title = document.createElement("div"); title.className = "t";
    title.textContent = ({text:"📝 ", link:"🔗 ", file:"📎 "}[e.kind] || "") + (e.title || (e.kind === "file" ? "" : e.text.slice(0, 300)));
    if (!e.title && e.kind !== "file") title.style.whiteSpace = "pre-wrap";
    d.append(title);
    if (e.kind !== "file" && e.title && e.text !== e.title){ const c = document.createElement("div"); c.className = "c"; c.textContent = e.text; d.append(c); }
    if (e.kind === "file"){ const c = document.createElement("div"); c.className = "c"; c.dir = "ltr"; c.style.textAlign = "start"; c.textContent = fmt(e.size); d.append(c); }
    const acts = document.createElement("div"); acts.className = "a";
    const mk = (label, fn, primary) => { const b = document.createElement("button"); b.className = "btn" + (primary ? "" : " ghost"); b.textContent = t(label); b.onclick = fn; acts.append(b); };
    if (e.kind === "file") mk("download", () => download(e), true);
    else {
      mk("copy", () => { copyText(e.text); if (!e.got){ e.got = true; got(e.id); renderInbox(); } }, e.kind !== "link");
      if (e.kind === "link") mk("open", () => { if (!e.got){ e.got = true; got(e.id); } window.open(e.text, "_blank", "noopener"); }, true);
    }
    d.append(acts); box.append(d);
  });
}

// ---------------------------------------------------------------- wiring
$$(".tab").forEach(b => b.onclick = () => {
  $$(".tab").forEach(x => x.classList.toggle("active", x === b));
  $$("[data-pane]").forEach(p => p.classList.toggle("hidden", p.dataset.pane !== b.dataset.tab));
  $("#history").classList.toggle("hidden", b.dataset.tab === "inbox" || !hist().length);
  if (b.dataset.tab === "inbox") loadInbox();
  if (b.dataset.tab === "clip") loadClips();
});
$$("[data-send]").forEach(b => b.onclick = () => sendJson(b.dataset.send));
$("#camBtn").onclick = () => $("#camInput").click();
$("#pickBtn").onclick = () => $("#fileInput").click();
$("#camInput").onchange = e => { addFiles(e.target.files); e.target.value = ""; };
$("#fileInput").onchange = e => { addFiles(e.target.files); e.target.value = ""; };
$("#sendFiles").onclick = sendFiles;
$("#refreshInbox").onclick = loadInbox;
$("#pairBtn").onclick = () => pair($("#codeInput").value);
$("#codeInput").onkeydown = e => { if (e.key === "Enter") pair(e.target.value); };
$("#forget").onclick = () => { store.del("sh_token"); token = ""; showPair(); };
$("#langBtn").onclick = () => { lang = lang === "ar" ? "en" : "ar"; store.set("sh_lang", lang); applyLang(); renderInstall();
  if (section === "desk") renderDesk(); };


// ================================================================ phone app sections
let caps = {}, section = "send", lastEv = Number(store.get("sh_ev") || 0);
const sleep = ms => new Promise(r => setTimeout(r, ms));
function buzz(pattern){  // only after a real touch (browsers block it otherwise)
  try { if (navigator.vibrate && (!navigator.userActivation || navigator.userActivation.hasBeenActive)) navigator.vibrate(pattern); } catch(e){}
}
const post = (path, body) => api(path, {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(body || {})});
const secOn = s => s === "send" || (s === "desk" ? caps.desktop : s === "files" ? caps.files : s === "study" ? caps.study
                                    : s === "media" ? caps.sound : (caps.screen || caps.remote));
const secLocked = s => (caps.need_https || []).some(k => (s === "files" && k === "files") || (s === "media" && k === "sound")
                                                       || (s === "ctrl" && (k === "screen" || k === "remote")));
function renderAccess(){
  const app = window.StudyHubApp;
  if (!app || !app.phoneAccess || !caps.status){ $("#accessBar").classList.add("hidden"); return; }
  let a = {}; try { a = JSON.parse(app.phoneAccess()); } catch(e){}
  let later = {}; try { later = JSON.parse(store.get("sh_acc_later") || "{}"); } catch(e){}
  const need = !a.notif ? "notif" : !a.dnd ? "dnd" : (!a.type && !later.type) ? "type" : "";
  $("#accessBar").classList.toggle("hidden", !need);
  if (!need) return;
  $("#accText").textContent = t({notif: "accNotif", dnd: "accDnd", type: "accType"}[need]);
  $("#accBtn").textContent = t("accAllow");
  $("#accBtn").onclick = () => app.openAccess(need);
  const lb = $("#accLater");  // typing is optional: it can wait (the laptop asks again when you use it)
  lb.classList.toggle("hidden", need !== "type"); lb.textContent = t("accLater");
  lb.onclick = () => { later.type = 1; store.set("sh_acc_later", JSON.stringify(later)); renderAccess(); };
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) renderAccess(); });
let syncConf = null, syncAt = 0;
async function renderSync(force){  // in the app only: photo access + the phone folder for new synced folders
  const app = window.StudyHubApp, box = $("#syncBars");
  if (!app || !app.pickFolder || !caps.sync){ box.innerHTML = ""; return; }
  if (force || !syncConf || Date.now() - syncAt > 30000){
    try { const r = await api("/api/sync/config"); if (r.status === 200){ syncConf = r.body; syncAt = Date.now(); } } catch(e){}
  }
  if (!syncConf) return;
  let linked = []; try { linked = JSON.parse(app.linkedFolders()); } catch(e){}
  box.innerHTML = "";
  const bar = (title, text, btn, fn) => {
    const d = document.createElement("div"); d.className = "labbar";
    d.innerHTML = `<div class="hx"><b></b><small></small></div><button class="go"></button>`;
    d.querySelector("b").textContent = title; d.querySelector("b").style.whiteSpace = "normal";
    d.querySelector("small").textContent = text;
    d.querySelector("button").textContent = btn; d.querySelector("button").onclick = fn; box.append(d);
  };
  if (syncConf.backup && syncConf.backup.on && !app.mediaAccess()) bar(t("syncTitle"), t("syncMedia"), t("accAllow"), () => app.requestMedia());
  for (const f of syncConf.folders || [])
    if (!linked.includes(f.id)) bar(t("syncPick", f.name), t("syncPickHint"), t("syncPickBtn"), () => app.pickFolder(f.id));
}
window.renderSync = renderSync;
document.addEventListener("visibilitychange", () => { if (!document.hidden) renderSync(true); });
function applyCaps(c){
  caps = c || {};
  renderAccess();
  renderSync();
  $$(".nav button").forEach(b => { const s = b.dataset.sec;
    b.classList.toggle("hidden", !secOn(s) && !secLocked(s)); b.classList.toggle("locked", !secOn(s) && secLocked(s)); });
  $$('[data-seg="ctrl"] button').forEach(b => b.classList.toggle("hidden", b.dataset.v === "screen" ? !caps.screen : !caps.remote));
  if (section !== "send" && !secOn(section) && !secLocked(section)) showSec("send");
}
function showSec(s){
  if (typeof exitFull === "function") exitFull();
  section = s; store.set("sh_sec", s);
  $$(".nav button").forEach(b => b.classList.toggle("on", b.dataset.sec === s));
  const locked = s !== "send" && !secOn(s) && secLocked(s);
  $$("[data-sec-pane]").forEach(p => p.classList.toggle("hidden", p.dataset.secPane !== (locked ? "locked" : s)));
  window.scrollTo({top: 0});
  if (locked){ renderLocked(); return; }
  ({desk: loadDesk, files: loadFiles, study: () => segShow("study", (store.get("sh_seg_study") || "tasks").replace("timer", "tasks")),
    media: () => segShow("media", store.get("sh_seg_media") || "listen"),
    ctrl: () => segShow("ctrl", firstCtrl())})[s]?.();
}
function firstCtrl(){
  const want = store.get("sh_seg_ctrl") || "deck";
  const btn = $(`[data-seg="ctrl"] [data-v="${want}"]`);
  return btn && !btn.classList.contains("hidden") ? want : (caps.remote ? "deck" : "screen");
}
function renderLocked(){
  $("#lockedText").textContent = t("lockedText"); const go = $("#lockedGo"); go.innerHTML = "";
  if (token && INFO.https_port) go.append(el("a", {className:"btn", href:`https://${location.hostname}:${INFO.https_port}/#t=${encodeURIComponent(token)}`}, t("instOpen")));
  const b = el("button", {className:"btn ghost"}, "📲 " + t("instTitle")); b.onclick = () => { renderInstall(); $("#installView").classList.remove("hidden"); window.scrollTo({top:0}); };
  go.append(b);
}
function failed(r){
  if (r.status === 403 && r.body.error === "https"){ caps.need_https = [...(caps.need_https || []), "files", "screen", "remote"]; showSec(section); }
  else if (r.status === 403 && r.body.error === "off") toast(t("offCap"), "bad");
  else if (r.status !== 401) toast(t("failed"), "bad");
}
let segs = {};
function segShow(group, v){
  segs[group] = v; store.set("sh_seg_" + group, v);
  $$(`[data-seg="${group}"] button`).forEach(b => b.classList.toggle("on", b.dataset.v === v));
  $$(`[data-seg-pane^="${group}:"]`).forEach(p => p.classList.toggle("hidden", p.dataset.segPane !== group + ":" + v));
  if (group === "study") ({tasks: loadTasks, notes: loadNotes})[v]?.();
  if (group === "media" && v === "music") loadMusic();
  if (group === "ctrl"){ if (v === "screen") startScreen(); if (v === "deck") loadDeck(); }
}
$$("[data-seg] button").forEach(b => b.onclick = () => segShow(b.closest("[data-seg]").dataset.seg, b.dataset.v));
$$(".nav button").forEach(b => b.onclick = () => showSec(b.dataset.sec));

// ---------------------------------------------------------------- bottom sheet
function sheet(title, actions, body){
  const s = $("#sheet"), box = $("#sheetBox"); box.innerHTML = "";
  box.append(el("div", {className:"sh-t"}, title)); if (body) box.append(body);
  actions.forEach(([label, fn, primary]) => { const b = el("button", {className:"btn mt" + (primary ? "" : " ghost")}, label);
    b.onclick = () => { closeSheet(); fn(); }; box.append(b); });
  s.classList.remove("hidden"); requestAnimationFrame(() => s.classList.add("show"));
}
function closeSheet(){ const s = $("#sheet"); s.classList.remove("show"); setTimeout(() => s.classList.add("hidden"), 250); }
$("#sheet").onclick = e => { if (e.target.id === "sheet") closeSheet(); };

// ---------------------------------------------------------------- notifications (events from the laptop)
async function onEvents(list){
  if (!list.length) return;
  const newest = Math.max(...list.map(e => e.seq));
  if (lastEv) for (const ev of list){
    if (ev.kind === "camera"){ if (!document.hidden) camRequest(ev.text); continue; }
    if (ev.text) notify((ev.kind === "outbox" ? t("evOutbox") + ": " : "⏱ ") + ev.text);
  }
  lastEv = newest; store.set("sh_ev", String(lastEv));
}
async function notify(text){
  if (APP && document.hidden){ try { APP.notify("Study Hub", text); return; } catch(e){} }
  buzz([120, 60, 120]);
  if (window.Notification && Notification.permission === "granted" && document.hidden){
    try { const reg = navigator.serviceWorker && await navigator.serviceWorker.getRegistration();
      if (reg){ await reg.showNotification("Study Hub", {body: text, icon: "/icon-192.png", tag: "sh-" + Date.now()}); return; } } catch(e){}
  }
  toast(text, "ok");
}
function renderNotifyBtn(){
  const b = $("#notifyBtn"), granted = window.Notification && Notification.permission === "granted";
  b.textContent = !SECURE || !window.Notification ? t("noNotify") : granted ? t("notifyDone") : t("notifyOn");
  b.disabled = granted || !SECURE || !window.Notification;
}
$("#notifyBtn").onclick = async () => { try { await Notification.requestPermission(); } catch(e){} renderNotifyBtn(); };
// keep polling while the installed app sits in the background, so timer alerts still arrive
setInterval(() => { if (token && document.hidden && STANDALONE) ping(); }, 15000);

// ---------------------------------------------------------------- desktop
let desk = {desks: [], items: []}, curDesk = store.get("sh_desk") || "all";
const iconCache = {}, KIND_ICON = {app: "🧩", url: "🌐", folder: "📁", file: "📄", note: "🗒"};
async function loadDesk(){ const r = await api("/api/desktop"); if (r.status === 200){ desk = r.body; renderDesk(); } else failed(r); }
function renderDesk(){
  const chips = $("#deskChips"); chips.innerHTML = "";
  if (!desk.desks.some(d => d.id === curDesk)) curDesk = "all";
  desk.desks.forEach(d => { const c = el("button", {className: d.id === curDesk ? "on" : ""}, d.name);
    c.onclick = () => { curDesk = d.id; store.set("sh_desk", d.id); renderDesk(); }; chips.append(c); });
  const grid = $("#deskGrid"); grid.innerHTML = "";
  const items = desk.items.filter(i => i.desk === curDesk);
  if (!items.length) grid.append(el("div", {className:"empty", style:"grid-column:1/-1"}, t("deskEmpty")));
  items.forEach(i => {
    const b = el("button", {className:"tile"}), ic = el("div", {className:"ic"}, KIND_ICON[i.kind] || "•");
    if (i.kind === "note") ic.style.background = (i.color || "#fde68a") + "44"; else loadIcon(i.id, ic);
    b.append(ic, el("span", {}, i.title)); b.onclick = () => itemSheet(i); grid.append(b);
  });
}
async function loadIcon(id, box){
  if (!(id in iconCache)) iconCache[id] = fetch("/api/icon/" + id, {headers:{"X-Device": token}})
      .then(r => r.ok ? r.blob() : null).then(b => b && b.size ? URL.createObjectURL(b) : null).catch(() => null);
  const url = await iconCache[id]; if (url){ box.textContent = ""; box.append(el("img", {src: url, alt: ""})); }
}
async function openItem(id){ const r = await post("/api/desktop/open/" + id); if (r.status === 200) toast(t("openedLaptop"), "ok"); else failed(r); }
function itemSheet(i){
  const acts = [[t("openLaptop"), () => openItem(i.id), true]];
  let body = null;
  if (i.kind === "url") acts.push([t("openPhone"), () => window.open(i.url, "_blank", "noopener")]);
  if (i.kind === "file" && i.file && caps.files) acts.push([t("download"), () => fetchFile("/api/desktop/file/" + i.id, i.title)]);
  if (i.kind === "note"){ body = el("div", {className:"pre"}, i.text || ""); acts.length = 0;
    if (caps.study && i.note_id) acts.push([t("editNote"), () => { showSec("study"); segShow("study", "notes"); openNote(i.note_id); }, true]);
    acts.push([t("openLaptop"), () => openItem(i.id)]); }
  if (i.kind === "url") body = el("div", {className:"hint ltr", style:"overflow-wrap:anywhere"}, i.url);
  sheet(i.title, acts, body);
}
$("#deskRefresh").onclick = loadDesk;

// ---------------------------------------------------------------- shared folders
let fsLoc = null;
const fsIcon = n => /\.(jpe?g|png|gif|webp|bmp|heic)$/i.test(n) ? "🖼" : /\.pdf$/i.test(n) ? "📕" : /\.(docx?|odt|rtf)$/i.test(n) ? "📘"
  : /\.(pptx?|key)$/i.test(n) ? "📙" : /\.(xlsx?|csv)$/i.test(n) ? "📗" : /\.(zip|rar|7z|tar|gz)$/i.test(n) ? "🗜"
  : /\.(mp4|mkv|mov|avi)$/i.test(n) ? "🎬" : /\.(mp3|wav|m4a|flac)$/i.test(n) ? "🎵" : "📄";
function fsRow(icon, name, meta){ const b = el("button", {className:"frow"}), fn = el("div", {className:"fn"});
  fn.append(el("b", {}, name)); if (meta) fn.append(el("small", {}, meta)); b.append(el("span", {className:"fi"}, icon), fn); return b; }
async function loadFiles(){
  if (fsLoc) return openDir(fsLoc.root, fsLoc.path);
  const r = await api("/api/fs"); if (r.status !== 200){ failed(r); return; }
  $("#fsCrumbs").innerHTML = ""; $("#fsUpload").classList.add("hidden");
  const list = $("#fsList"); list.innerHTML = "";
  if (!r.body.roots.length) list.append(el("div", {className:"card empty"}, t("noShared")));
  r.body.roots.forEach(x => { const row = fsRow("📁", x.name, ""); row.onclick = () => openDir(x.i, ""); list.append(row); });
}
async function openDir(root, path){
  const r = await api(`/api/fs?root=${root}&path=${encodeURIComponent(path)}`);
  if (r.status !== 200){ if (r.status === 404 && fsLoc){ fsLoc = null; return loadFiles(); } failed(r); return; }
  fsLoc = {root, path: r.body.path, name: r.body.name};
  const cr = $("#fsCrumbs"); cr.innerHTML = "";
  const home = el("button", {}, "⌂"); home.onclick = () => { fsLoc = null; loadFiles(); }; cr.append(home);
  const parts = fsLoc.path ? fsLoc.path.split("/") : [];
  [fsLoc.name, ...parts].forEach((name, i) => { cr.append(document.createTextNode("›"));
    const b = el("button", {}, name); b.onclick = () => openDir(root, parts.slice(0, i).join("/")); cr.append(b); });
  const list = $("#fsList"); list.innerHTML = "";
  if (!r.body.entries.length) list.append(el("div", {className:"empty"}, t("folderEmpty")));
  r.body.entries.forEach(e => {
    const rel = (fsLoc.path ? fsLoc.path + "/" : "") + e.name;
    const row = fsRow(e.dir ? "📁" : fsIcon(e.name), e.name, e.dir ? "" : fmt(e.size) + " · " + new Date(e.mtime * 1000).toLocaleDateString());
    row.onclick = () => e.dir ? openDir(root, rel) : fileSheet(root, rel, e.name); list.append(row);
  });
  $("#fsUpload").classList.remove("hidden");
}
function fileSheet(root, rel, name){
  const q = `root=${root}&path=${encodeURIComponent(rel)}`, acts = [];
  if (/\.(jpe?g|png|gif|webp|bmp)$/i.test(name)) acts.push([t("preview"), () => viewImage("/api/fs/get?inline=1&" + q), true]);
  if (/\.(mp3|m4a|aac|wav|flac|ogg|opus)$/i.test(name)) acts.push([t("playHere"), () => playHere("/api/fs/get?inline=1&" + q, name), true]);
  acts.push([t("download"), () => fetchFile("/api/fs/get?" + q, name), !acts.length]);
  acts.push([t("openLaptop"), async () => { const r = await post("/api/fs/open", {root, path: rel}); if (r.status === 200) toast(t("openedLaptop"), "ok"); else failed(r); }]);
  sheet(name, acts);
}
async function viewImage(path){
  const r = await fetch(path, {headers:{"X-Device": token}}); if (!r.ok){ toast(t("failed"), "bad"); return; }
  const url = URL.createObjectURL(await r.blob()), v = el("div", {className:"viewer"}); v.append(el("img", {src: url}));
  v.onclick = () => { v.remove(); URL.revokeObjectURL(url); }; document.body.append(v);
}
$("#fsUpload").onclick = () => $("#fsInput").click();
$("#fsInput").onchange = async e => {
  const files = [...e.target.files]; e.target.value = ""; if (!fsLoc) return; let ok = 0;
  for (const file of files){
    if (file.size > INFO.max_mb * 1048576){ toast(t("tooBig", INFO.max_mb), "bad"); continue; }
    const f = {file, el: {fill: {style: {}}, size: {}}};
    const s = await upload(f, null, {"X-Root": String(fsLoc.root), "X-Dir": encodeURIComponent(fsLoc.path)});
    if (s === 200) ok++; else if (s === 403) failed({status: 403, body: {error: "https"}});
  }
  if (ok){ toast(t("uploaded", ok), "ok"); openDir(fsLoc.root, fsLoc.path); }
};


// ---------------------------------------------------------------- study: tasks
async function loadTasks(){
  const r = await api("/api/tasks"); if (r.status !== 200){ failed(r); return; }
  const list = $("#taskList"); list.innerHTML = "";
  if (!r.body.tasks.length) list.append(el("div", {className:"card empty"}, t("tasksEmpty")));
  r.body.tasks.forEach(x => {
    const row = el("div", {className:"task"}), ck = el("button", {className:"ck"}), txt = el("div", {style:"flex:1;min-width:0"});
    txt.append(el("div", {}, x.title));
    const meta = el("small"); if (x.subject){ const dot = el("span", {className:"pill"}); dot.style.background = x.color || "var(--accent)"; meta.append(dot, x.subject + "  "); }
    if (x.due) meta.append(x.due); txt.append(meta);
    ck.onclick = async () => { ck.classList.add("on"); const res = await post("/api/tasks/" + x.id, {done: true});
      if (res.status === 200){ toast(t("taskDone"), "ok"); row.classList.add("done"); setTimeout(() => row.remove(), 380); } else { ck.classList.remove("on"); failed(res); } };
    row.append(ck, txt); list.append(row);
  });
}
$("#taskAdd").onclick = () => { showSec("send"); $('.tab[data-tab="task"]').click(); $("#taskTitle").focus(); };

// ---------------------------------------------------------------- study: notes
let editing = null, noteTimer = 0;
async function loadNotes(){
  if (editing) return;
  const q = $("#noteSearch").value.trim();
  const r = await api("/api/notes" + (q ? "?q=" + encodeURIComponent(q) : "")); if (r.status !== 200){ failed(r); return; }
  const list = $("#noteList"); list.innerHTML = "";
  if (!r.body.notes.length) list.append(el("div", {className:"empty"}, t("notesEmpty")));
  r.body.notes.forEach(n => {
    const row = fsRow(n.locked ? "🔒" : n.pinned ? "📌" : "📝", n.title || "—",
                      (n.subject ? n.subject + " · " : "") + (n.snippet || "").replace(/\s+/g, " ").slice(0, 90));
    row.onclick = () => n.locked ? toast(t("lockedNote"), "bad") : openNote(n.id); list.append(row);
  });
}
async function openNote(id){
  post("/api/handoff", {kind: "note", note_id: id}).catch(() => {});  // the laptop can continue it
  const r = await api("/api/notes/" + id);
  if (r.status === 423){ toast(t("lockedNote"), "bad"); return; }
  if (r.status !== 200){ failed(r); return; }
  editing = r.body; $("#notesBrowse").classList.add("hidden"); $("#noteEdit").classList.remove("hidden");
  $("#noteEditTitle").value = editing.title; $("#noteEditText").value = editing.content; $("#noteState").textContent = "";
}
function closeNote(){ editing = null; $("#noteEdit").classList.add("hidden"); $("#notesBrowse").classList.remove("hidden"); loadNotes(); }
async function saveNote(){
  if (!editing) return;
  const body = {title: $("#noteEditTitle").value.trim(), content: $("#noteEditText").value, base: editing.updated_at};
  const r = await post("/api/notes/" + editing.id, body);
  if (r.status === 200){ editing.updated_at = r.body.updated_at; editing.content = body.content; editing.title = body.title;
    $("#noteState").textContent = t("saved"); toast(t("saved"), "ok"); }
  else if (r.status === 409){ toast(t("changed"), "bad"); const id = editing.id; editing = null; openNote(id); }
  else failed(r);
}
$("#noteSave").onclick = saveNote;
$("#noteBack").onclick = () => { if (editing && $("#noteEditText").value !== editing.content && !confirm(t("unsaved"))) return; closeNote(); };
$("#noteEditText").oninput = () => $("#noteState").textContent = t("unsaved");
$("#noteSearch").oninput = () => { clearTimeout(noteTimer); noteTimer = setTimeout(loadNotes, 300); };
$("#noteNew").onclick = () => { showSec("send"); $('.tab[data-tab="note"]').click(); $("#noteText").focus(); };

// ---------------------------------------------------------------- control: keys, touchpad, typing
const inp = data => post("/api/input", data).then(r => { if (r.status !== 200) failed(r); return r; }).catch(() => {});
$$("[data-key]").forEach(b => b.onclick = () => { buzz(15); inp({type: "key", action: b.dataset.key}); });
$("#typeSend").onclick = async () => { const text = $("#typeText").value; if (!text) return;
  if ((await inp({type: "text", text}))?.status === 200){ $("#typeText").value = ""; toast(t("sent"), "ok"); } };
$("#lClick").onclick = () => inp({type: "click", button: "left"});
$("#rClick").onclick = () => inp({type: "click", button: "right"});
const pad = $("#pad"), pts = new Map(); let acc = {dx: 0, dy: 0, sy: 0}, sending = false, moved = 0, downAt = 0, most = 0;
pad.onpointerdown = e => { pad.setPointerCapture(e.pointerId); pts.set(e.pointerId, {x: e.clientX, y: e.clientY});
  if (pts.size === 1){ moved = 0; downAt = Date.now(); most = 1; } most = Math.max(most, pts.size); };
pad.onpointermove = e => {
  const p = pts.get(e.pointerId); if (!p) return;
  const dx = e.clientX - p.x, dy = e.clientY - p.y; p.x = e.clientX; p.y = e.clientY; moved += Math.abs(dx) + Math.abs(dy);
  if (pts.size >= 2) acc.sy += dy / pts.size; else { const k = 1.4 + Math.min(2, Math.hypot(dx, dy) / 12); acc.dx += dx * k; acc.dy += dy * k; }
  flush();
};
pad.onpointerup = pad.onpointercancel = e => { pts.delete(e.pointerId);
  if (!pts.size && moved < 10 && Date.now() - downAt < 280) inp({type: "click", button: most >= 2 ? "right" : "left"}); };
async function flush(){
  if (sending) return; sending = true;
  try {
    while (Math.abs(acc.dx) >= 1 || Math.abs(acc.dy) >= 1 || Math.abs(acc.sy) >= 4){
      const a = acc; acc = {dx: 0, dy: 0, sy: 0};
      if (Math.abs(a.dx) >= 1 || Math.abs(a.dy) >= 1) await inp({type: "move", dx: Math.round(a.dx), dy: Math.round(a.dy)});
      if (Math.abs(a.sy) >= 4) await inp({type: "scroll", dy: -Math.round(a.sy * 4)}); else acc.sy += a.sy;
    }
  } finally { sending = false; }
}

// ---------------------------------------------------------------- control: screen
let live = true, zoomed = false, monitor = Number(store.get("sh_mon") || 0), screenRun = 0, shotUrl = null;
function screenVisible(){ return section === "ctrl" && segs.ctrl === "screen" && !document.hidden; }
async function startScreen(){
  $("#screenHint").textContent = caps.remote ? t("screenHint") : t("screenOnly");
  const run = ++screenRun;
  do {
    const t0 = Date.now(); await grabScreen();
    await sleep(Math.max(120, (full ? 550 : 800) - (Date.now() - t0)));
  } while (run === screenRun && live && screenVisible());
}
async function grabScreen(){
  const dpr = window.devicePixelRatio || 1;
  const w = Math.min(2560, Math.round(full ? Math.max(screen.width, screen.height) * dpr
                                          : $("#screenBox").clientWidth * dpr * (zoomed ? 2 : 1)));
  try {
    const r = await fetch(`/api/screen?m=${monitor}&w=${w}`, {headers:{"X-Device": token}});
    if (!r.ok){ let b = {}; try { b = await r.json(); } catch(e){} failed({status: r.status, body: b}); live = false; $("#liveBtn").classList.remove("on"); return; }
    const n = Number(r.headers.get("X-Monitors") || 1), sel = $("#monSel");
    if (sel.options.length !== n){ sel.innerHTML = ""; for (let i = 0; i < n; i++) sel.append(el("option", {value: i}, "🖥 " + (i + 1))); sel.value = monitor; }
    sel.classList.toggle("hidden", n < 2);
    const url = URL.createObjectURL(await r.blob()), img = $("#screenImg");
    img.onload = () => { if (shotUrl && shotUrl !== url) URL.revokeObjectURL(shotUrl); shotUrl = url; }; img.src = url;
  } catch(e){ setStatus(false); }
}
// where on the laptop screen a tap landed (the picture may be letter-boxed in full screen)
function screenPoint(e){
  const img = $("#screenImg"), r = img.getBoundingClientRect(), iw = img.naturalWidth, ih = img.naturalHeight;
  if (!iw || !ih) return null;
  const k = Math.min(r.width / iw, r.height / ih), dw = iw * k, dh = ih * k;
  const x = (e.clientX - r.left - (r.width - dw) / 2) / dw, y = (e.clientY - r.top - (r.height - dh) / 2) / dh;
  return x < 0 || x > 1 || y < 0 || y > 1 ? null : {x, y};
}
let pressTimer = 0, longPressed = false, rightNext = false;
async function tapAt(pt, button){
  await inp({type: "tap", m: monitor, x: pt.x, y: pt.y, button});
  buzz(button === "right" ? 40 : 12);
  if (!live) setTimeout(grabScreen, 250);
}
$("#screenImg").onpointerdown = e => {
  longPressed = false; clearTimeout(pressTimer);
  if (!caps.remote) return;
  const pt = screenPoint(e);
  if (pt) pressTimer = setTimeout(() => { longPressed = true; tapAt(pt, "right"); }, 550);
};
$("#screenImg").onpointermove = e => { if (Math.abs(e.movementX) + Math.abs(e.movementY) > 6) clearTimeout(pressTimer); };
$("#screenImg").onpointerup = $("#screenImg").onpointercancel = () => clearTimeout(pressTimer);
$("#screenImg").oncontextmenu = e => e.preventDefault();
$("#screenImg").onclick = async e => {
  if (!caps.remote || longPressed) return;
  const pt = screenPoint(e); if (!pt) return;
  const button = rightNext ? "right" : "left";
  if (rightNext){ rightNext = false; $("#fsRight").classList.remove("on"); }
  tapAt(pt, button);
};


// ---------------------------------------------------------------- the laptop's clipboard history
let clipTimer = 0;
async function loadClips(){
  const can = caps.clipboard, locked = (caps.need_https || []).includes("clipboard");
  $("#clipHist").classList.toggle("hidden", !can); $("#clipLocked").classList.toggle("hidden", can || !locked);
  if (!can) return;
  const q = $("#clipSearch").value.trim();
  const r = await api("/api/clips" + (q ? "?q=" + encodeURIComponent(q) : "")); if (r.status !== 200){ failed(r); return; }
  const list = $("#clipList"); list.innerHTML = "";
  if (!r.body.enabled) list.append(el("div", {className:"hint"}, t("clipOff")));
  if (!r.body.clips.length) list.append(el("div", {className:"empty"}, t("clipsEmpty")));
  r.body.clips.forEach(c => {
    const row = el("div", {className:"clip"}), body = el("div", {className:"cx"});
    if (c.kind === "image"){
      const img = el("img", {alt: ""}); body.append(img);
      fetch(`/api/clips/${c.id}/image`, {headers:{"X-Device": token}}).then(x => x.ok ? x.blob() : null)
        .then(b => { if (b) img.src = URL.createObjectURL(b); }).catch(() => {});
    } else { body.textContent = (c.pinned ? "📌 " : "") + ({link: "🔗 ", files: "📁 "}[c.kind] || "") + (c.text || c.preview); body.dir = "auto"; }
    body.append(el("small", {}, c.at.slice(5, 16).replace("T", " ")));
    const more = el("button", {className:"cb"}, "⋯"); more.onclick = () => clipSheet(c);
    if (c.kind === "text" || c.kind === "link"){ const cp = el("button", {className:"cb"}, t("copy")); cp.onclick = () => copyText(c.text); row.append(body, cp, more); }
    else row.append(body, more);
    list.append(row);
  });
}
async function clipDo(c, action){
  const r = await post("/api/clips/" + c.id, {action});
  if (r.status === 200){ if (action === "copy") toast(t("onLaptopClip"), "ok"); else loadClips(); } else failed(r);
}
function clipSheet(c){
  const acts = [[t("toLaptop"), () => clipDo(c, "copy"), true]];
  if (c.kind === "text" || c.kind === "link") acts.unshift([t("toPhone"), () => copyText(c.text), false]);
  if (c.kind === "link") acts.push([t("openPhone"), () => window.open(c.text.trim(), "_blank", "noopener")]);
  if (c.kind === "image") acts.push([t("download"), () => fetchFile(`/api/clips/${c.id}/image`, "clipboard.png")]);
  acts.push([c.pinned ? t("unpin") : t("pin"), () => clipDo(c, c.pinned ? "unpin" : "pin")], [t("del"), () => clipDo(c, "delete")]);
  sheet((c.preview || "").slice(0, 120), acts, c.text && c.text.length > 120 ? el("div", {className:"pre", dir:"auto"}, c.text) : null);
}
$("#clipSearch").oninput = () => { clearTimeout(clipTimer); clipTimer = setTimeout(loadClips, 300); };
$("#clipMore").onclick = loadClips;

// ---------------------------------------------------------------- laptop sound, live on the phone
let actx = null, gain = null, listenAbort = null;
function setListen(on, msg){
  $("#listenBtn").classList.toggle("on", on); $("#listenBtn").textContent = on ? "■" : "▶";
  $("#listenState").textContent = msg || (on ? t("listening") : t("listenOff"));
}
async function listen(){
  if (listenAbort){ listenAbort.abort(); listenAbort = null; setListen(false); return; }
  actx = actx || new (window.AudioContext || window.webkitAudioContext)({latencyHint: "interactive"});
  actx.resume().catch(() => {});  // don't wait: some browsers only resume after a real tap
  if (!gain){ gain = actx.createGain(); gain.connect(actx.destination); }
  gain.gain.value = Number($("#listenVol").value) / 100;
  const abort = listenAbort = new AbortController(); setListen(true);
  if ("mediaSession" in navigator) navigator.mediaSession.metadata = new MediaMetadata({title: "Study Hub", artist: INFO.name});
  try {
    const r = await fetch("/api/audio/live", {headers:{"X-Device": token}, signal: abort.signal});
    if (!r.ok){ let b = {}; try { b = await r.json(); } catch(e){} listenAbort = null; setListen(false, t("listenFail"));
      if (r.status !== 503) failed({status: r.status, body: b}); return; }
    const rate = Number(r.headers.get("X-Rate")) || 48000, ch = Number(r.headers.get("X-Channels")) || 2, fb = 2 * ch;
    const reader = r.body.getReader(); let carry = new Uint8Array(0), next = 0;
    const min = Math.round(rate * 0.04) * fb;  // play in ~40 ms pieces
    while (true){
      const {value, done} = await reader.read(); if (done) break;
      const buf = new Uint8Array(carry.length + value.length); buf.set(carry); buf.set(value, carry.length);
      if (buf.length < min){ carry = buf; continue; }
      const usable = buf.length - buf.length % fb;
      const pcm = new Int16Array(buf.slice(0, usable).buffer); carry = buf.slice(usable);
      const frames = pcm.length / ch, ab = actx.createBuffer(ch, frames, rate);
      for (let c = 0; c < ch; c++){ const d = ab.getChannelData(c); for (let i = 0; i < frames; i++) d[i] = pcm[i * ch + c] / 32768; }
      const src = actx.createBufferSource(); src.buffer = ab; src.connect(gain);
      const now = actx.currentTime;
      if (next < now + 0.02 || next > now + 0.5) next = now + 0.12;  // fell behind / drifted: re-anchor (keeps the delay low)
      src.start(next); next += ab.duration;
    }
  } catch(e){ if (e.name !== "AbortError") setStatus(false); }
  if (listenAbort === abort){ listenAbort = null; setListen(false); }
}
$("#listenBtn").onclick = listen;
$("#listenVol").oninput = e => { if (gain) gain.gain.value = Number(e.target.value) / 100; };

// ---------------------------------------------------------------- phone music on the laptop
let music = null, musicAt = 0;
const mmss = s => Math.floor(s / 60) + ":" + String(Math.floor(s % 60)).padStart(2, "0");
async function loadMusic(action, value){
  const r = action ? await post("/api/music", {action, value}) : await api("/api/music");
  if (r.status === 200){ music = r.body; musicAt = Date.now(); renderMusic(); } else failed(r);
}
function renderMusic(){
  if (!music) return;
  const playing = music.index >= 0 ? music.queue[music.index] : "";
  let pos = music.position; if (music.playing) pos = Math.min(music.duration || pos, pos + (Date.now() - musicAt) / 1000);
  $("#npTitle").textContent = playing || t("nothingPlaying");
  $("#npBar").style.width = (music.duration ? pos / music.duration * 100 : 0) + "%";
  $("#npPos").textContent = mmss(pos); $("#npDur").textContent = mmss(music.duration || 0);
  $("#npToggle").textContent = music.playing ? "⏸" : "▶";
  if (document.activeElement !== $("#npVol")) $("#npVol").value = music.volume;
  const q = $("#npQueue"); q.innerHTML = "";
  music.queue.forEach((name, i) => { const row = el("button", {className:"q frow" + (i === music.index ? " on" : "")});
    row.append(el("span", {className:"fi"}, i === music.index ? (music.playing ? "🔊" : "⏸") : "🎵"), el("span", {}, name));
    row.onclick = () => loadMusic("play", i); q.append(row); });
  $("#npClear").classList.toggle("hidden", !music.queue.length);
}
setInterval(() => { if (section === "media" && segs.media === "music" && !document.hidden){
  renderMusic(); if (Date.now() - musicAt > 4000) loadMusic(); } }, 1000);
$("#npToggle").onclick = () => loadMusic("toggle");
$("#npNext").onclick = () => loadMusic("next");
$("#npPrev").onclick = () => loadMusic("prev");
$("#npClear").onclick = () => loadMusic("clear");
$("#npVol").onchange = e => loadMusic("volume", Number(e.target.value));
$("#npBar").parentElement.onclick = e => { if (!music || !music.duration) return;
  const r = e.currentTarget.getBoundingClientRect(); loadMusic("seek", (e.clientX - r.left) / r.width * music.duration); };
$("#musicPick").onclick = () => $("#musicInput").click();
$("#musicInput").onchange = async e => {
  const files = [...e.target.files]; e.target.value = "";
  for (const [i, file] of files.entries()){
    $("#musicUp").textContent = t("uploading", `${i + 1}/${files.length} · ${file.name}`);
    const res = await new Promise(resolve => {
      const x = new XMLHttpRequest(); x.open("POST", "/api/music/upload");
      x.setRequestHeader("X-Device", token); x.setRequestHeader("X-Filename", encodeURIComponent(file.name));
      x.setRequestHeader("Content-Type", "application/octet-stream");
      x.upload.onprogress = ev => { if (ev.lengthComputable) $("#musicUp").textContent = t("uploading", `${i + 1}/${files.length} · ${Math.round(ev.loaded / ev.total * 100)}%`); };
      x.onload = () => { let b = {}; try { b = JSON.parse(x.responseText); } catch(err){} resolve({status: x.status, body: b}); };
      x.onerror = () => resolve({status: 0, body: {}}); x.send(file);
    });
    if (res.status === 200){ music = res.body; musicAt = Date.now(); renderMusic(); }
    else if (res.status === 413) toast(t("tooBig", res.body.max_mb), "bad"); else failed(res);
  }
  $("#musicUp").textContent = "";
};

// ---------------------------------------------------------------- play a laptop music file here
async function playHere(path, name){
  const r = await fetch(path, {headers:{"X-Device": token}}); if (!r.ok){ toast(t("failed"), "bad"); return; }
  const url = URL.createObjectURL(await r.blob()), box = el("div");
  const audio = el("audio", {src: url, controls: true, autoplay: true}); box.append(audio);
  sheet("🎵 " + name, [[t("back"), () => { audio.pause(); URL.revokeObjectURL(url); }]], box);
}

// ---------------------------------------------------------------- this phone on the laptop (scrcpy)
$("#mirrorBtn").onclick = async () => {
  const r = await post("/api/mirror", {action: "start"});
  if (r.status === 200) toast(r.body.found ? t("mirrorOk") : t("mirrorNo"), r.body.found ? "ok" : "bad"); else failed(r);
};


// ---------------------------------------------------------------- continue where you left off
let handoff = {}, hoDismissed = JSON.parse(store.get("sh_ho_x") || "{}");
function renderHandoff(h){
  handoff = h || {};
  $("#standBtn").classList.toggle("hidden", !caps.study);
  const box = $("#handoff"); box.innerHTML = "";
  if (!caps.study) return;
  const fresh = x => x && Date.now() / 1000 - x.at < 12 * 3600 && hoDismissed[x.kind] !== x.at;
  const row = (kind, icon, title, sub, act) => {
    const d = el("div", {className: "ho"}), tx = el("div", {className: "hx"});
    tx.append(el("b", {}, title), el("small", {}, sub));
    const go = el("button", {className: "go"}, t("openHere")); go.onclick = act;
    const x = el("button", {className: "x"}, "×");
    x.onclick = () => { hoDismissed[kind] = handoff[kind].at; store.set("sh_ho_x", JSON.stringify(hoDismissed)); renderHandoff(handoff); };
    d.append(el("span", {style: "font-size:22px"}, icon), tx, go, x); box.append(d);
  };
  if (fresh(handoff.pdf && {...handoff.pdf, kind: "pdf"}))
    row("pdf", "📄", handoff.pdf.name, `💻 ${t("hoPdf")} · ${t("hoPage", handoff.pdf.page + 1)} / ${handoff.pdf.pages}`,
        () => openPdfHere(handoff.pdf.page));
  if (fresh(handoff.note && {...handoff.note, kind: "note"}))
    row("note", "📝", handoff.note.title || "—", "💻 " + t("hoNote"), () => { showSec("study"); segShow("study", "notes"); openNote(handoff.note.id); });
}
let pv = {page: 0, pages: 0, timer: 0, url: null};
async function openPdfHere(page){
  if (!SECURE){ toast(t("hoSecure"), "bad"); return; }
  $("#pdfView").classList.remove("hidden"); $("#pvName").textContent = handoff.pdf.name;
  pv.pages = handoff.pdf.pages; showPage(page);
}
async function showPage(n){
  pv.page = Math.max(0, Math.min(n, (pv.pages || 1) - 1));
  $("#pvNum").textContent = `${pv.page + 1} / ${pv.pages || "?"}`;
  $("#pvSub").textContent = t("hoPage", pv.page + 1);
  const w = Math.min(2200, Math.round(innerWidth * (devicePixelRatio || 1) * ($("#pdfView").classList.contains("z") ? 1.8 : 1)));
  try {
    const r = await fetch(`/api/handoff/page?n=${pv.page}&w=${w}`, {headers: {"X-Device": token}});
    if (!r.ok){ let b = {}; try { b = await r.json(); } catch(e){} if (b.error === "https") toast(t("hoSecure"), "bad"); else toast(t("failed"), "bad"); return; }
    pv.pages = Number(r.headers.get("X-Pages")) || pv.pages; $("#pvNum").textContent = `${pv.page + 1} / ${pv.pages}`;
    const url = URL.createObjectURL(await r.blob()), img = $("#pvImg");
    img.onload = () => { if (pv.url) URL.revokeObjectURL(pv.url); pv.url = url; }; img.src = url;
    $("#pvPages").scrollTo({top: 0});
  } catch(e){ setStatus(false); }
  clearTimeout(pv.timer);  // tell the laptop where we are (after a short pause)
  pv.timer = setTimeout(() => post("/api/handoff", {kind: "pdf", page: pv.page}).catch(() => {}), 1500);
}
$("#pvPrev").onclick = () => showPage(pv.page - 1);
$("#pvNext").onclick = () => showPage(pv.page + 1);
$("#pvClose").onclick = () => $("#pdfView").classList.add("hidden");
$("#pvZoom").onclick = () => { $("#pdfView").classList.toggle("z"); showPage(pv.page); };
$("#pvLaptop").onclick = async () => { const r = await post("/api/handoff", {kind: "pdf", page: pv.page}); toast(r.status === 200 ? t("sentLaptop") : t("failed"), r.status === 200 ? "ok" : "bad"); };
(() => {  // swipe left / right to turn pages
  let x0 = null, y0 = 0;
  $("#pvPages").addEventListener("touchstart", e => { if (e.touches.length === 1){ x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; } }, {passive: true});
  $("#pvPages").addEventListener("touchend", e => {
    if (x0 === null || $("#pdfView").classList.contains("z")) return;
    const dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0; x0 = null;
    if (Math.abs(dx) > 70 && Math.abs(dx) > Math.abs(dy) * 1.5) showPage(pv.page + (dx < 0 ? 1 : -1));
  }, {passive: true});
})();

// ---------------------------------------------------------------- stand mode (phone next to the laptop)
let stand = {on: false, data: null, at: 0, lock: null, lastDown: 0};
async function openStand(){
  stand.on = true; $("#stand").classList.remove("hidden");
  try { await $("#stand").requestFullscreen({navigationUI: "hide"}); } catch(e){}
  try { if (screen.orientation && screen.orientation.lock) await screen.orientation.lock("landscape"); } catch(e){}
  try { if ("wakeLock" in navigator) stand.lock = await navigator.wakeLock.request("screen"); } catch(e){}
  try { APP && APP.keepAwake(true); } catch(e){}
  tickStand(); loadStand();
}
function closeStand(){
  stand.on = false; $("#stand").classList.add("hidden");
  try { APP && APP.keepAwake(false); } catch(e){}
  try { stand.lock && stand.lock.release(); } catch(e){} stand.lock = null;
  try { screen.orientation && screen.orientation.unlock && screen.orientation.unlock(); } catch(e){}
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
}
async function loadStand(){
  const r = await api("/api/stand"); if (r.status !== 200){ failed(r); return; }
  stand.data = r.body; stand.at = Date.now(); renderStand();
}
function renderStand(){
  const d = stand.data; if (!d) return;
  const tm = d.timer; let left = tm.remaining; if (tm.state === "running") left = Math.max(0, left - (Date.now() - stand.at) / 1000);
  $("#stTimer").textContent = mmss(left); $("#stPhase").textContent = t(tm.phase) + " · 🍅 " + tm.done_today;
  const c = d.next_class;
  $("#stClass").textContent = c ? c.subject : t("stNoClass");
  $("#stClassMeta").textContent = c ? `${c.start}–${c.end}${c.location ? " · " + c.location : ""}` : "";
  const ul = $("#stTasks"); ul.innerHTML = "";
  (d.tasks.length ? d.tasks : [{title: t("stNoTasks")}]).forEach(x => ul.append(el("li", {}, "• " + x.title)));
  const sv = d.servers;
  $("#stSrvT").style.display = sv && sv.total ? "" : "none";
  if (sv && sv.total){
    $("#stSrv").innerHTML = ""; const dot = el("span", {className: "dot"}); dot.style.background = sv.down ? "#f87171" : sv.warn ? "#fbbf24" : "#34d399";
    $("#stSrv").append(dot, document.createTextNode(sv.down ? t("stDown", sv.down) : `${sv.up}/${sv.total} · ${t("stAllUp")}`));
    $("#stSrvDown").textContent = sv.down_names.join(" · ");
    $("#stand").classList.toggle("alert", sv.down > 0);
    if (sv.down > stand.lastDown) buzz([200, 100, 200, 100, 400]);
    stand.lastDown = sv.down;
  } else $("#stand").classList.remove("alert");
  $("#stBat").textContent = d.battery ? `🔋 ${d.battery.percent}%${d.battery.charging ? " · " + t("stCharging") : ""}` : INFO.name;
  $("#stMusic").textContent = d.music ? `${d.music.playing ? "🔊" : "⏸"} ${d.music.title}` : "";
  $("#stLabT").style.display = d.lab ? "" : "none";
  if (d.lab){ const m = Math.floor((Date.now() / 1000 - d.lab.since) / 60); $("#stLab").textContent = `● ${d.lab.title} · ${m}′`; }
}
function tickStand(){
  const now = new Date();
  $("#stClock").textContent = now.toLocaleTimeString(lang === "ar" ? "ar-SY" : "en-GB", {hour: "2-digit", minute: "2-digit"});
  $("#stDay").textContent = now.toLocaleDateString(lang === "ar" ? "ar-SY" : "en-GB", {weekday: "long", day: "numeric", month: "long"});
}
setInterval(() => { if (!stand.on) return; tickStand(); renderStand(); if (Date.now() - stand.at > 5000 && !document.hidden) loadStand(); }, 1000);
$("#standBtn").onclick = openStand;
$("#standClose").onclick = closeStand;
document.addEventListener("visibilitychange", async () => {  // the screen lock is dropped when the page hides
  if (!document.hidden && stand.on && "wakeLock" in navigator){ try { stand.lock = await navigator.wakeLock.request("screen"); } catch(e){} }
});

// ---------------------------------------------------------------- control: screen in full screen
let full = false;
async function enterFull(){
  const box = $("#screenBox"); full = true; box.classList.add("full");
  $("#fsExit").classList.remove("hidden"); $("#fsRight").classList.toggle("hidden", !caps.remote);
  try {
    if (box.requestFullscreen) await box.requestFullscreen({navigationUI: "hide"});
    else if (box.webkitRequestFullscreen) box.webkitRequestFullscreen();
    if (screen.orientation && screen.orientation.lock) await screen.orientation.lock("landscape");
  } catch(e){}  // no Fullscreen API (iPhone) / no rotation lock: the page still fills the window
  if (!live){ live = true; $("#liveBtn").classList.add("on"); startScreen(); } else grabScreen();
}
function exitFull(){
  if (!full) return;
  full = false; $("#screenBox").classList.remove("full");
  $("#fsExit").classList.add("hidden"); $("#fsRight").classList.add("hidden");
  try { if (screen.orientation && screen.orientation.unlock) screen.orientation.unlock(); } catch(e){}
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  else if (document.webkitFullscreenElement) document.webkitExitFullscreen();
  grabScreen();
}
$("#fullBtn").onclick = enterFull;
$("#fsExit").onclick = exitFull;
$("#fsRight").onclick = () => { rightNext = !rightNext; $("#fsRight").classList.toggle("on", rightNext); };
["fullscreenchange", "webkitfullscreenchange"].forEach(ev => document.addEventListener(ev, () => {
  if (!document.fullscreenElement && !document.webkitFullscreenElement) exitFull(); }));
window.addEventListener("popstate", exitFull);
$("#liveBtn").onclick = () => { live = !live; $("#liveBtn").classList.toggle("on", live); if (live) startScreen(); else grabScreen(); };
$("#zoomBtn").onclick = () => { zoomed = !zoomed; $("#zoomBtn").classList.toggle("on", zoomed); $("#screenBox").classList.toggle("z", zoomed); grabScreen(); };
$("#monSel").onchange = e => { monitor = Number(e.target.value); store.set("sh_mon", String(monitor)); grabScreen(); };
document.addEventListener("visibilitychange", () => { if (!document.hidden && screenVisible() && live) startScreen(); });

// ---------------------------------------------------------------- install as an app
const SECURE = location.protocol === "https:";
const STANDALONE = matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
let installPrompt = null;
window.addEventListener("beforeinstallprompt", e => { e.preventDefault(); installPrompt = e; renderInstall(); });
window.addEventListener("appinstalled", () => { installPrompt = null; toast(t("instDone"), "ok"); renderInstall(); });
function el(tag, attrs, text){ const n = document.createElement(tag); Object.assign(n, attrs || {}); if (text) n.textContent = text; return n; }
function renderInstall(){
  const box = $("#installView"); box.innerHTML = "";
  box.append(el("h2", {style:"font-size:17px;margin:0 0 6px"}, t("instTitle")));
  if (STANDALONE || APP){ box.append(el("p", {className:"hint"}, APP ? t("instApp") : t("instDone"))); return; }
  const apk = el("a", {className:"btn ghost", href:"/app.apk", download:"StudyHub.apk"}, "🤖 " + t("instApk"));
  box.append(el("p", {className:"hint"}, t("instApkHint")), apk);
  if (SECURE){
    if (installPrompt){
      const b = el("button", {className:"btn"}, t("instNow"));
      b.onclick = async () => { installPrompt.prompt(); await installPrompt.userChoice; installPrompt = null; renderInstall(); };
      box.append(b);
    }
    box.append(el("p", {className:"hint"}, t("instHttps")), el("p", {className:"hint"}, t("instIos")));
    return;
  }
  const ol = el("ol", {className:"steps"});
  const li1 = el("li"); li1.append(el("b", {}, t("inst1")), el("a", {className:"btn ghost", href:"/ca.crt", download:"StudyHub-CA.crt"}, t("instCert")));
  const li2 = el("li", {}, t("inst2"));
  const li3 = el("li"); li3.append(el("b", {}, t("inst3")));
  if (token && INFO.https_port){
    li3.append(el("a", {className:"btn", href:`https://${location.hostname}:${INFO.https_port}/#t=${encodeURIComponent(token)}`}, t("instOpen")));
  } else li3.append(el("div", {className:"hint"}, t("instPair")));
  ol.append(li1, li2, li3, el("li", {}, t("inst4")));
  box.append(ol, el("div", {className:"note"}, t("instNote")), el("p", {className:"hint"}, t("instIos")));
}
$("#installBtn").onclick = () => { renderInstall(); $("#installView").classList.toggle("hidden"); };

// ---------------------------------------------------------------- shared from another app (share_target)
async function processShare(){
  if (!("caches" in window) || !location.search.includes("share=1")) return;
  window.history.replaceState(null, "", "/");
  const cache = await caches.open("share-inbox");
  const metaRes = await cache.match("/share-meta");
  if (!metaRes) return;
  const meta = await metaRes.json();
  let sent = 0;
  const text = (meta.text || "").trim();
  const link = (meta.url || "").trim() || (/^https?:\/\/\S+$/.test(text) ? text : "");
  const post = (kind, body) => api("/api/" + kind, {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(body)});
  try {
    if (link){ if ((await post("link", {url: link, title: meta.title || ""})).status === 200) sent++; }
    else if (text){ if ((await post("note", {text, title: meta.title || ""})).status === 200) sent++; }
    for (const f of meta.files || []){
      const res = await cache.match(f.key);
      if (!res) continue;
      const blob = await res.blob();
      const item = {file: new File([blob], f.name || "shared", {type: blob.type}), el: {fill: {style: {}}, size: {}}};
      if (await upload(item, null) === 200){ sent++; remember("file", f.name); }
      await cache.delete(f.key);
    }
  } catch(e){}
  await cache.delete("/share-meta");
  toast(sent ? t("shareSent", sent) : t("failed"), sent ? "ok" : "bad");
}
document.addEventListener("visibilitychange", () => { if (!document.hidden && token) ping(); });

(async function init(){
  applyLang();
  if (SECURE && "serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
  const moved = location.hash.match(/t=([A-Za-z0-9_\-%]+)/);
  if (moved){  // came from the http page's "Open the secure app": same device, same token
    token = decodeURIComponent(moved[1]); store.set("sh_token", token);
    window.history.replaceState(null, "", location.pathname + location.search);
  }
  setInterval(() => { if (token && !document.hidden) ping(); }, 8000);
  const m = location.hash.match(/k=([A-Za-z0-9-]+)/);
  if (m){  // opened from the QR code: the code is in the #fragment, which is never sent over the network
    window.history.replaceState(null, "", location.pathname);
    if (!token || !(await ping())){ $("#nameInput").value = store.get("sh_name") || guessName(); if (await pair(m[1])) return; }
  }
  if (!token){ showPair(); return; }
  if (await ping() || token) showMain();  // offline: keep the forms, the status dot says so
  appSession();
  const open = location.hash.match(/open=(\w+)/);  // the app's shortcuts: #open=stand / ctrl / media / study
  if (open){ window.history.replaceState(null, "", location.pathname);
    if (open[1] === "stand") openStand(); else if (open[1].startsWith("cam")) camRequest(open[1].slice(3)); else showSec(open[1]); }
  const sec = store.get("sh_sec"); if (!open && sec && sec !== "send" && !location.search.includes("share=1")) showSec(sec);
  processShare();
})();

// ---------------------------------------------------------------- lab running: photos straight into the report
let lab = {}, cam = {stream: null, facing: "environment", sent: 0, mode: "lab"};  // mode: lab | photo | scan
function renderLab(l){
  lab = l || {};
  $("#labBar").classList.toggle("hidden", !lab.title);
  $("#labTitle").textContent = lab.title || "";
  if (!lab.title && !$("#cam").classList.contains("hidden")){ closeCam(); toast(t("labEnded")); }
}
function sendLabPhoto(blob, name){
  const caption = $("#camCaption").value.trim();
  const f = {file: new File([blob], name, {type: blob.type || "image/jpeg"}), progress: 0, el: {fill: {style: {}}}};
  const thumb = document.createElement("i"); thumb.className = "wait";
  thumb.style.backgroundImage = `url(${URL.createObjectURL(blob)})`;
  $("#camShots").prepend(thumb);
  $("#camCaption").value = "";
  const extra = {"X-Lab": "1"}; if (caption) extra["X-Caption"] = encodeURIComponent(caption);
  upload(f, null, extra).then(status => {
    thumb.className = status === 200 ? "ok" : "bad";
    if (status === 200){ cam.sent++; $("#camCount").textContent = t("camSent", cam.sent); if (navigator.vibrate) navigator.vibrate(30); }
    else toast(t("camFail"), "bad");
  });
}
function sendCameraShot(blob, name){  // photo / scan for the laptop: one shot, then the camera closes
  const f = {file: new File([blob], name, {type: blob.type || "image/jpeg"}), progress: 0, el: {fill: {style: {}}}};
  const mode = cam.mode;
  upload(f, null, {"X-Camera": mode}).then(status => {
    if (status === 200){ toast(t(mode === "scan" ? "camScanSent" : "camPhotoSent"), "ok"); if (navigator.vibrate) navigator.vibrate(30); }
    else toast(t("camFail"), "bad");
  });
  closeCam();
}
function camRequest(mode){  // the laptop asked for a photo / scan (or a button here)
  if (!["photo", "scan"].includes(mode)) return;
  if (!$("#cam").classList.contains("hidden") && cam.mode === mode) return;
  if (!$("#cam").classList.contains("hidden")) closeCam();
  openCam(mode);
}
window.camRequest = camRequest;
async function startStream(){
  if (cam.stream) cam.stream.getTracks().forEach(tr => tr.stop());
  const big = cam.mode !== "lab";  // photos and scans: as sharp as the camera allows
  cam.stream = await navigator.mediaDevices.getUserMedia({video: {facingMode: cam.facing,
    width: {ideal: big ? 4096 : 1920}, height: {ideal: big ? 3072 : 1080}}, audio: false});
  $("#camVideo").srcObject = cam.stream;
}
async function openCam(mode){
  cam.mode = typeof mode === "string" ? mode : "lab";
  if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)){ $("#labCapture").click(); return; }  // plain http: the camera app
  $("#cam").classList.remove("hidden");
  $("#camTitle").textContent = cam.mode === "lab" ? (lab.title || "") : t(cam.mode === "scan" ? "camScanTitle" : "camPhotoTitle");
  $("#camCaption").classList.toggle("hidden", cam.mode !== "lab");
  $("#camFrame").classList.toggle("hidden", cam.mode !== "scan");
  $("#camCaption").placeholder = t("camCaption"); $("#camCount").textContent = cam.sent ? t("camSent", cam.sent) : "";
  try { await startStream(); }
  catch(err){ closeCam(); toast(t("camNoCam"), "bad"); $("#labCapture").click(); }
}
function closeCam(){
  if (cam.stream) cam.stream.getTracks().forEach(tr => tr.stop());
  cam.stream = null; $("#cam").classList.add("hidden"); $("#camShots").innerHTML = ""; cam.sent = 0;
}
$("#labCamBtn").onclick = () => openCam("lab");
$("#toClipPhoto").onclick = () => camRequest("photo");
$("#toClipScan").onclick = () => camRequest("scan");
$("#camClose").onclick = closeCam;
$("#camFlip").onclick = () => { cam.facing = cam.facing === "environment" ? "user" : "environment"; startStream().catch(() => toast(t("camNoCam"), "bad")); };
$("#camShutter").onclick = () => {
  const v = $("#camVideo"); if (!v.videoWidth) return;
  const c = document.createElement("canvas"); c.width = v.videoWidth; c.height = v.videoHeight;
  c.getContext("2d").drawImage(v, 0, 0);
  const fl = $("#camFlash"); fl.style.transition = "none"; fl.style.opacity = ".8";
  requestAnimationFrame(() => { fl.style.transition = "opacity .25s"; fl.style.opacity = "0"; });
  const stamp = new Date().toISOString().slice(0, 19).replace(/[T:]/g, "-");
  if (cam.mode === "lab") c.toBlob(b => b && sendLabPhoto(b, `lab-${stamp}.jpg`), "image/jpeg", 0.9);
  else c.toBlob(b => b && sendCameraShot(b, `${cam.mode}-${stamp}.jpg`), "image/jpeg", 0.95);
};
$("#camCaption").onkeydown = e => { if (e.key === "Enter") $("#camShutter").click(); };
$("#labCapture").onchange = e => {  // fallback: the phone's camera app, one photo at a time
  const file = e.target.files[0]; e.target.value = ""; if (!file) return;
  const f = {file, progress: 0, el: {fill: {style: {}}}};
  const head = cam.mode === "lab" ? {"X-Lab": "1"} : {"X-Camera": cam.mode};
  upload(f, null, head).then(status => {
    if (status === 200) toast(cam.mode === "lab" ? t("camSent", 1) : t(cam.mode === "scan" ? "camScanSent" : "camPhotoSent"), "ok");
    else toast(t("camFail"), "bad");
  });
};

// ---------------------------------------------------------------- control: the Deck (set up on the laptop)
let deckTimer = 0;
function deckVisible(){ return section === "ctrl" && (segs.ctrl || "deck") === "deck" && !document.hidden; }
function renderDeck(d){
  const g = $("#deckGrid"); g.innerHTML = "";
  for (const b of d.buttons || []){
    const el = document.createElement("button"); el.className = "deckb" + (b.on ? " on" : "");
    el.style.background = `linear-gradient(145deg, ${b.color}, ${b.color}aa)`;
    el.innerHTML = `<span class="e"></span><span class="l"></span>`;
    el.querySelector(".e").textContent = b.emoji; el.querySelector(".l").textContent = b.label;
    if (d.volume && b.kind === "media" && /vol|🔊|🔉/.test(b.emoji)){
      const lv = document.createElement("span"); lv.className = "lvl"; lv.innerHTML = `<i style="width:${d.volume.level}%"></i>`; el.append(lv);
    }
    el.onclick = () => pressDeck(b);
    g.append(el);
  }
  $("#deckHint").textContent = (d.buttons || []).length ? t("deckHint") : t("deckEmpty");
}
async function loadDeck(){
  clearTimeout(deckTimer);
  if (!caps.remote) return;
  try { const r = await api("/api/deck"); if (r.status === 200) renderDeck(r.body); } catch(e){}
  if (deckVisible()) deckTimer = setTimeout(loadDeck, 4000);  // live volume / mute / OBS
}
async function pressDeck(b){
  if (b.confirm && !confirm(t("deckSleep"))) return;
  buzz(18);
  const r = await post("/api/deck/press", {id: b.id}).catch(() => null);
  if (r && r.status === 200) renderDeck(r.body); else toast((r && r.body && r.body.error && r.body.error.length < 60) ? r.body.error : t("deckFail"), "bad");
}

// ---------------------------------------------------------------- air mouse / laser: hold and move the phone
let air = {on: false, dx: 0, dy: 0, last: 0, laser: false};
function airMotion(e){
  if (!air.on) return;
  const r = e.rotationRate; if (!r) return;
  const now = performance.now(), dt = air.last ? Math.min(60, now - air.last) / 1000 : 0; air.last = now;
  const k = air.laser ? 22 : 16;  // degrees turned -> pixels
  air.dx += -(r.alpha || 0) * dt * k; air.dy += -(r.beta || 0) * dt * k;
  if (Math.abs(air.dx) >= 1 || Math.abs(air.dy) >= 1){ acc.dx += air.dx; acc.dy += air.dy; air.dx = air.dy = 0; flush(); }
}
function airStart(btn, laser){
  if (!("DeviceMotionEvent" in window)){ toast(t("airNo"), "bad"); return; }
  air = {on: true, dx: 0, dy: 0, last: 0, laser}; btn.classList.add("on"); buzz(12);
  window.addEventListener("devicemotion", airMotion);
}
function airStop(btn){
  air.on = false; btn.classList.remove("on"); window.removeEventListener("devicemotion", airMotion);
}
for (const [id, laser] of [["airBtn", false], ["laserBtn", true]]){
  const btn = $("#" + id);
  btn.onpointerdown = e => { e.preventDefault(); btn.setPointerCapture(e.pointerId); airStart(btn, laser); };
  btn.onpointerup = btn.onpointercancel = () => airStop(btn);
}
</script>
</body>
</html>
"""


def render(info: dict) -> str:
    lang = info.get("lang", "ar")
    safe = json.dumps({"name": info.get("name", ""), "max_mb": info.get("max_mb", 50), "lang": lang,
                       "https_port": info.get("https_port", 0)})
    safe = safe.replace("<", "\\u003c")  # never close the <script> tag
    return (_PAGE.replace("__LANG__", html.escape(lang)).replace("__DIR__", "rtl" if lang == "ar" else "ltr")
            .replace("__INFO__", safe))


def manifest() -> str:
    """Web app manifest: home-screen icon, full screen, and a target in Android's Share menu."""
    return json.dumps({
        "name": "Study Hub", "short_name": "Study Hub", "id": "/", "start_url": "/", "scope": "/",
        "display": "standalone", "background_color": "#0d0e1a", "theme_color": "#0d0e1a",
        "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
                  {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
                  {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
        "share_target": {"action": "/share", "method": "POST", "enctype": "multipart/form-data",
                         "params": {"title": "title", "text": "text", "url": "url",
                                    "files": [{"name": "files", "accept": ["*/*"]}]}},
    }, ensure_ascii=False)


# The service worker: keeps the page available offline and receives things shared from other apps
# (it stores them, then opens the page, which sends them to the laptop with this device's token).
SERVICE_WORKER = r"""
const SHELL = "studyhub-shell-v2";
self.addEventListener("install", e => { e.waitUntil(caches.open(SHELL).then(c => c.addAll(["/"]))); self.skipWaiting(); });
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("notificationclick", e => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({type: "window"}).then(list => list.length ? list[0].focus() : self.clients.openWindow("/")));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method === "POST" && url.pathname === "/share") { e.respondWith(keepShare(e.request)); return; }
  if (e.request.method === "GET" && url.pathname === "/" ) {
    e.respondWith(fetch(e.request).then(r => { const copy = r.clone(); caches.open(SHELL).then(c => c.put("/", copy)); return r; })
                  .catch(() => caches.match("/")));
  }
});
async function keepShare(request) {
  const form = await request.formData();
  const cache = await caches.open("share-inbox");
  const meta = {title: form.get("title") || "", text: form.get("text") || "", url: form.get("url") || "", files: []};
  let i = 0;
  for (const f of form.getAll("files")) {
    if (!f || typeof f === "string") continue;
    const key = "/share-file/" + (i++);
    await cache.put(key, new Response(f, {headers: {"Content-Type": f.type || "application/octet-stream"}}));
    meta.files.push({key, name: f.name, size: f.size});
  }
  await cache.put("/share-meta", new Response(JSON.stringify(meta), {headers: {"Content-Type": "application/json"}}));
  return Response.redirect("/?share=1", 303);
}
"""
