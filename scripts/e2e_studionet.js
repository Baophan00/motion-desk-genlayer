#!/usr/bin/env node
/**
 * A whole motion on GenLayer Studio Network.
 *
 *   node scripts/e2e_studionet.js <contractAddress> [motionBody]
 *
 * The body is prose. One consensus round reads it into a PAY action, three keys
 * vote, the tally waits for the voting window to close, and execution waits for
 * the timelock. Both waits are real: the script sleeps, it does not skip them.
 * Two guards are exercised too: a vote after the window, and a second execute.
 */
const fs = require("fs");
const path = require("path");
const { createClient, createAccount, generatePrivateKey } = require("genlayer-js");
const { studionet } = require("genlayer-js/chains");

const ADDRESS = process.argv[2];
if (!ADDRESS) {
  console.error("usage: node scripts/e2e_studionet.js <contractAddress> [motionBody]");
  process.exit(1);
}

const BODY =
  process.argv[3] ||
  "Pay 250 credits to the gardener at 0x1234567890AbCdEf1234567890aBcDeF12345678 " +
    "for the spring work on the common garden: soil, seeds and the two benches.";

const VOTE_WINDOW = 300; // seconds
const TIMELOCK = 120; // seconds
const SETTLE_BUFFER = 8; // seconds past a deadline before the next call

const KEY_PATH = process.env.MD_KEYS || path.join(__dirname, ".motion-keys.json");
const keys = JSON.parse(fs.readFileSync(KEY_PATH, "utf8"));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const parties = {
  proposer: createClient({ chain: studionet, account: createAccount(keys.proposer) }),
  voter_a: createClient({ chain: studionet, account: createAccount(keys.voter_a) }),
  voter_b: createClient({ chain: studionet, account: createAccount(keys.voter_b) }),
  voter_c: createClient({ chain: studionet, account: createAccount(keys.voter_c) }),
};
const watcher = createClient({ chain: studionet, account: createAccount(generatePrivateKey()) });

async function retrying(label, fn) {
  let last;
  for (let attempt = 0; attempt < 12; attempt += 1) {
    try {
      return await fn();
    } catch (error) {
      const message = String(error && error.message);
      const rateLimited = /rate limit|429|too many requests/i.test(message);
      if (
        !rateLimited &&
        !/DOCTYPE|fetch failed|ECONNRESET|timeout|temporarily unavailable|not available/i.test(message)
      ) {
        throw error;
      }
      last = error;
      const wait = rateLimited ? 30000 : 4000;
      console.log(`  ${label}: ${rateLimited ? "rate limited" : "rpc hiccup"}, retry ${attempt + 1} in ${wait / 1000}s`);
      await sleep(wait);
    }
  }
  throw last;
}

function executionFailed(receipt) {
  const leader = (receipt.consensus_data && receipt.consensus_data.leader_receipt) || [];
  return String(leader[0] && leader[0].execution_result).toUpperCase() === "ERROR";
}

async function call(role, fn, args) {
  console.log(`\n[${role}] ${fn}(${args.map((a) => JSON.stringify(a).slice(0, 64)).join(", ")})`);
  const hash = await retrying("send", () =>
    parties[role].writeContract({ address: ADDRESS, functionName: fn, args, value: 0n })
  );
  console.log(`  tx ${hash}`);
  const receipt = await retrying("receipt", () =>
    parties[role].waitForTransactionReceipt({ hash, status: "FINALIZED", retries: 240, interval: 6000 })
  );
  const failed = executionFailed(receipt);
  console.log(`  ${receipt.status_name || receipt.status}  (${failed ? "refused by the contract" : "executed"})`);
  return { hash, failed };
}

async function must(role, fn, args) {
  const out = await call(role, fn, args);
  if (out.failed) throw new Error(`${fn} was refused`);
  return out.hash;
}

const view = (fn, args) =>
  retrying("read", () => watcher.readContract({ address: ADDRESS, functionName: fn, args }));

async function sleepUntil(deadline, label) {
  const wait = deadline * 1000 - Date.now() + SETTLE_BUFFER * 1000;
  if (wait > 0) {
    console.log(`\n  waiting ${Math.round(wait / 1000)}s for ${label}`);
    await sleep(wait);
  }
}

(async () => {
  console.log("contract", ADDRESS);
  console.log("body    ", BODY);

  console.log("\n[proposer] submit the motion as prose");
  const known = await view("stats", []);
  const motionId = known.motions + 1;
  await must("proposer", "submit_motion", [BODY, VOTE_WINDOW, TIMELOCK]);
  console.log(`  motion id ${motionId}`);

  let motion = await view("get_motion", [motionId]);
  console.log(`  draft | window ${motion.vote_window}s | timelock ${motion.timelock}s`);

  console.log("\n[proposer] structure: one consensus round reads the prose");
  let t0 = Date.now();
  await must("proposer", "structure", [motionId]);
  console.log(`  round took ${Math.round((Date.now() - t0) / 1000)}s`);

  motion = await view("get_motion", [motionId]);
  console.log(
    `  ${motion.status} | ${motion.action_type} | target ${motion.target} | value ${motion.value}`
  );
  console.log(`  summary: ${motion.summary}`);
  if (motion.status !== "voting") throw new Error(`the motion did not reach voting: ${motion.status} ${motion.reason}`);

  console.log("\n[voter_a] vote yes");
  await must("voter_a", "vote", [motionId, "yes"]);
  console.log("[voter_b] vote yes");
  await must("voter_b", "vote", [motionId, "yes"]);
  console.log("[voter_c] vote yes");
  await must("voter_c", "vote", [motionId, "yes"]);

  motion = await view("get_motion", [motionId]);
  console.log(`  yes ${motion.yes} | no ${motion.no} | abstain ${motion.abstain}`);

  console.log("\n[guard] tally before the window closes is refused");
  const early = await call("voter_a", "tally", [motionId]);
  if (!early.failed) throw new Error("tally was allowed before the window closed");

  await sleepUntil(motion.voting_ends, "the voting window to close");

  console.log("\n[voter_a] tally");
  const tallied = await call("voter_a", "tally", [motionId]);
  if (tallied.failed) throw new Error("tally was refused");
  motion = await view("get_motion", [motionId]);
  console.log(`  ${motion.status} | yes ${motion.yes} | no ${motion.no} | turnout ${motion.yes + motion.no + motion.abstain}`);
  if (motion.status !== "passed") throw new Error(`the motion did not pass: ${motion.status}`);

  console.log("\n[guard] execute before the timelock is refused");
  const earlyExec = await call("voter_b", "execute", [motionId]);
  if (!earlyExec.failed) throw new Error("execute was allowed before the timelock expired");

  console.log("\n[guard] a vote after the window is refused");
  const lateVote = await call("voter_c", "vote", [motionId, "no"]);
  if (!lateVote.failed) throw new Error("a vote after the window was accepted");

  await sleepUntil(motion.execute_after, "the timelock to expire");

  console.log("\n[voter_c] execute");
  await must("voter_c", "execute", [motionId]);

  motion = await view("get_motion", [motionId]);
  console.log(`  status ${motion.status} | executed_at ${motion.executed_at}`);
  if (motion.status !== "executed") throw new Error(`the motion was not executed: ${motion.status}`);

  console.log("\n[guard] a second execute is refused");
  const again = await call("voter_a", "execute", [motionId]);
  if (!again.failed) throw new Error("a second execute was accepted");

  console.log("\nlatest motions");
  const list = await view("list_motions", [0, 5]);
  for (const row of list) {
    console.log(`  motion ${row.id} ${row.status} ${row.action_type} ${row.target} value ${row.value}`);
  }
  console.log("\nstats");
  console.log(JSON.stringify(await view("stats", []), null, 2));

  console.log(
    "\nRESULT: one prose motion structured by a real consensus round, three votes, a refused early tally, a refused late vote, a passed tally, a refused early execute, and one execution behind the timelock"
  );
})().catch((error) => {
  console.error("\nFAILED:", error.message);
  if (error.cause) console.error(String(error.cause).slice(0, 900));
  process.exit(1);
});
