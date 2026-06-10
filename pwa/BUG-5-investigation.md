# BUG-5 调查报告: Friends button click timeout

## 真因

非 PWA 本身 stacking / z-index / modal backdrop bug。

playwright headless 真测 5 张 friend cards 上的 `+ Add Friend` / Borrow / Lend
3 类按钮全 < 80ms 内成功 click (1500ms 内无 timeout)。

## 原始症状来源

之前用户看到 click "timeout" 是 BUG-1 / BUG-3 的次生效应:

- BUG-1 Borrow submit TypeError on undefined.slice → SolidJS error boundary
  把对应路由整片 freeze (尤其 inflight 卡片 destroy 时); 用户接着 click
  其他按钮 timeout, 误判 button 本身有 stacking 问题。
- BUG-3 friend/add POST 404 → modal 卡 `submitting=true` 不退, modal backdrop
  仍 active, click outside / click 其他 friend button 会被 backdrop 拦截
  (modal absolute z-50 + bg-black/50 inset-0 真有覆盖)。

## 验证

BUG-1 + BUG-3 + BUG-4 修完后, 在 /friends 页:

```
[bug5] Add Friend click OK in 80ms
[bug5] borrowBtns: 5, lendBtns: 5
[bug5] borrow[0] click OK in 33ms (will navigate)
[bug5] borrow[1] click OK in 66ms (will navigate)
[bug5] borrow[2] click OK in 57ms (will navigate)
[bug5] lend[0] click OK in 36ms
[bug5] lend[1] click OK in 55ms
[bug5] lend[2] click OK in 55ms
TOTAL FAILURES: 0
```

全 < 80ms。1500ms timeout 上界富余 20x。

## 结论

BUG-5 不存在独立 PWA bug; 由 BUG-1/3 修复连带消除。

留这份文档作回归测试参考 + 未来 contributor 自查 z-index 时不再误改。
