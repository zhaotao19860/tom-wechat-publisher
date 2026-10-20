// 真实链路：2026-10-20 用 dig +trace / dig +norec 逐级抓取，两条查询共用一个 BIND9 缓存
export type Node = { id: string; zone: string; servers: string };

export type Step = {
  kind: "client" | "auth" | "reply";
  node?: string; // 目标权威节点 id
  qcard: string; // BIND9 此刻正在解析的名字
  qtag?: string; // qcard 角标
  q: string; // 查询报文关键信息
  r: string; // 应答报文关键信息
  rType: "referral" | "answer" | "priming" | "final";
  note: string; // 底部字幕
  cache?: string; // 本步写入的记录
  db?: "cache" | "adb"; // 写到哪个库，默认 cachedb
  hit?: string; // 本步命中的缓存（按前缀匹配）
};

export type Chain = {
  title: string;
  nodes: Node[];
  carry: string[]; // 上一条查询留下的缓存
  steps: Step[];
};

const ROOT = "a~m.root-servers.net";
const GTLD = "a~m.gtld-servers.net";

export const BAIDU: Chain = {
  title: "www.baidu.com",
  carry: [],
  nodes: [
    { id: "root1", zone: ".", servers: ROOT },
    { id: "root2", zone: ".", servers: ROOT },
    { id: "com1", zone: "com", servers: GTLD },
    { id: "baidu", zone: "baidu.com", servers: "ns1~4,7.baidu.com" },
    { id: "com2", zone: "com", servers: GTLD },
    { id: "shifen", zone: "shifen.com", servers: "dns,ns2~4.baidu.com" },
    { id: "ashifen", zone: "a.shifen.com", servers: "ns1~5.a.shifen.com" },
  ],
  steps: [
    { kind: "client", qcard: "www.baidu.com/A", q: "www.baidu.com/A  RD=1", r: "", rType: "answer", note: "客户端把 www.baidu.com 的 A 查询交给递归服务器 BIND9，RD=1 表示请你帮我查到底" },
    { kind: "auth", node: "root1", qcard: "./NS", qtag: "根预热", q: "./NS", r: ". NS a~m.root-servers.net（13 个）", rType: "priming", note: "冷启动先做根预热（priming）：用内置 hints 问根，拿到最新的 13 个根服务器名单", cache: ". NS ×13" },
    { kind: "auth", node: "root2", qcard: "www.baidu.com/A", q: "www.baidu.com/A", r: "referral → com. NS a~m.gtld-servers.net", rType: "referral", note: "根不知道答案，只回一个转介（referral）：去问 com 的 13 台 gTLD 服务器", cache: "com. NS ×13" },
    { kind: "auth", node: "com1", qcard: "www.baidu.com/A", q: "www.baidu.com/A", r: "referral → baidu.com. NS ns1~4,7.baidu.com", rType: "referral", note: "com 同样只做转介：baidu.com 由 ns1/2/3/4/7.baidu.com 负责", cache: "baidu.com. NS ×5" },
    { kind: "auth", node: "baidu", qcard: "www.baidu.com/A", q: "www.baidu.com/A", r: "AA  CNAME www.a.shifen.com  TTL 1200", rType: "answer", note: "baidu.com 权威给出 AA 应答，但不是 IP，而是别名 CNAME：www.a.shifen.com", cache: "www.baidu.com CNAME" },
    { kind: "auth", node: "com2", qcard: "www.a.shifen.com/A", qtag: "CNAME 重启", q: "www.a.shifen.com/A", r: "referral → shifen.com. NS dns,ns2~4.baidu.com", rType: "referral", note: "换个名字重新解析。com 的 NS 已在缓存里，直接跳过根，从 com 开始问", hit: "com. NS", cache: "shifen.com. NS ×4" },
    { kind: "auth", node: "shifen", qcard: "www.a.shifen.com/A", qtag: "CNAME 重启", q: "www.a.shifen.com/A", r: "referral → a.shifen.com. NS ns1~5.a.shifen.com", rType: "referral", note: "shifen.com 又把 a.shifen.com 子域委派了出去，这是一层多出来的区切割", cache: "a.shifen.com. NS ×5" },
    { kind: "auth", node: "ashifen", qcard: "www.a.shifen.com/A", qtag: "CNAME 重启", q: "www.a.shifen.com/A", r: "AA  A 220.181.111.232  TTL 120", rType: "answer", note: "终于拿到 A 记录：220.181.111.232，TTL 只有 120 秒，便于调度切换", cache: "www.a.shifen.com A" },
    { kind: "reply", qcard: "www.baidu.com/A", q: "", r: "CNAME www.a.shifen.com + A 220.181.111.232", rType: "final", note: "BIND9 把 CNAME 链和最终 A 记录一起返回客户端：1 次客户端查询，背后 7 次迭代" },
  ],
};

const INS = "ins-r23tsuuf.ias.tencent-cloud.net";

export const QQ: Chain = {
  title: "www.qq.com",
  carry: [". NS ×13", "com. NS ×13"],
  nodes: [
    { id: "com", zone: "com", servers: GTLD },
    { id: "qq1", zone: "qq.com", servers: "ns1~4.qq.com" },
    { id: "wwwqq", zone: "www.qq.com", servers: "ns-tel1,2.qq.com" },
    { id: "root", zone: ".", servers: ROOT },
    { id: "net", zone: "net", servers: GTLD },
    { id: "qq2", zone: "qq.com", servers: "ns1~4.qq.com" },
    { id: "tcloud", zone: "tencent-cloud.net", servers: "ns-open1~3.qq.com" },
  ],
  steps: [
    { kind: "client", qcard: "www.qq.com/A", q: "www.qq.com/A  RD=1", r: "", rType: "answer", note: "第二条查询 www.qq.com。缓存还热着，根和 com 的 NS 都在" },
    { kind: "auth", node: "com", qcard: "www.qq.com/A", q: "www.qq.com/A", r: "referral → qq.com. NS ns1~4.qq.com + glue", rType: "referral", note: "跳过根直接问 com。转介里附带了 ns1~4.qq.com 的 IP，这就是胶水记录（glue）", hit: "com. NS", cache: "qq.com. NS ×4" },
    { kind: "auth", node: "qq1", qcard: "www.qq.com/A", q: "www.qq.com/A", r: "referral → www.qq.com. NS ns-tel1,2.qq.com", rType: "referral", note: "qq.com 没有直接回答，它把 www.qq.com 单独委派给 ns-tel1 和 ns-tel2", cache: "www.qq.com. NS ×2" },
    { kind: "auth", node: "wwwqq", qcard: "www.qq.com/A", q: "www.qq.com/A", r: `AA  CNAME ${INS}`, rType: "answer", note: "www.qq.com 的权威给出别名，指向腾讯云 tencent-cloud.net 下的实例名，TTL 300", cache: "www.qq.com CNAME" },
    { kind: "auth", node: "root", qcard: "ins-r23tsuuf…/A", qtag: "CNAME 重启", q: `${INS}/A`, r: "referral → net. NS a~m.gtld-servers.net", rType: "referral", note: "新名字在 net 下，缓存里没有 net 的 NS，只好回到根", hit: ". NS", cache: "net. NS ×13" },
    { kind: "auth", node: "net", qcard: "ins-r23tsuuf…/A", qtag: "CNAME 重启", q: `${INS}/A`, r: "referral → tencent-cloud.net. NS ns-open1~3.qq.com", rType: "referral", note: "NS 名字都在 qq.com 下，属于区外名字，net 给不出它们的 IP（没有 glue）", cache: "tencent-cloud.net. NS ×3" },
    { kind: "auth", node: "qq2", qcard: "ns-open1.qq.com/A", qtag: "adb 查 NS 地址", q: "ns-open1.qq.com/A", r: "AA  A ×7（183.60.116.3 等）", rType: "answer", note: "BIND9 插进一次额外查询，向 qq.com 要 ns-open1 的地址，结果写进 adb（地址库）", hit: "qq.com. NS", cache: "ns-open1.qq.com A ×7", db: "adb" },
    { kind: "auth", node: "tcloud", qcard: "ins-r23tsuuf…/A", qtag: "CNAME 重启", q: `${INS}/A`, r: "AA  CNAME tra-kbagmtra… + A 42.81.179.153", rType: "answer", note: "tencent-cloud.net 又给一层别名 tra-kbagmtra，同一个应答里带上 A 42.81.179.153", cache: "ins-r23tsuuf… CNAME+A" },
    { kind: "reply", qcard: "www.qq.com/A", q: "", r: "CNAME ×2 + A 42.81.179.153", rType: "final", note: "两层 CNAME 加最终地址返回客户端。同样 7 次迭代，其中 1 次专门去查 NS 自己的地址" },
  ],
};

export const CHAINS: Chain[] = [BAIDU, QQ];
