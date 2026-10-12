import test from 'node:test';import assert from 'node:assert/strict';import {ema,atr,vwap,evaluateSignal,buildTradePlan} from '../src/strategy.js';
const bars=Array.from({length:30},(_,i)=>({o:100+i*.2,h:100.3+i*.2,l:99.8+i*.2,c:100.2+i*.2,v:i===29?2400:1000,t:String(i)}));
test('indikátory vrací platné hodnoty',()=>{assert.ok(ema(bars.map(x=>x.c),9)>0);assert.ok(atr(bars)>0);assert.ok(vwap(bars)>0)});
test('široký spread blokuje vstup',()=>{const s=evaluateSignal(bars,{bid:100,ask:101},{maxSpreadBps:8});assert.equal(s.side,'WAIT');assert.match(s.reason,/Spread/)});
test('velikost pozice respektuje risk a buying power',()=>{const plan=buildTradePlan({signal:{side:'BUY',symbol:'SPY',price:500,atr:1},equity:100000,buyingPower:100000,settings:{riskPerTradePct:.1,stopAtrMultiplier:1,targetRMultiplier:1.5}});assert.equal(plan.qty,40);assert.equal(plan.riskCash,40);assert.equal(plan.target,501.5)});
