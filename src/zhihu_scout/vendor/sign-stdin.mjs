// Local stdin adapter. No filesystem writes, network, or credential logging.
import { signRequest } from './zse-signer.mjs';
let input = '';
try {
  for await (const chunk of process.stdin) {
    input += chunk;
    if (input.length > 65536) throw new Error();
  }
  const { url, dc0 } = JSON.parse(input);
  process.stdout.write(JSON.stringify({ 'x-zse-93': '101_3_3.0', 'x-zse-96': signRequest(url, dc0) }));
} catch {
  process.stderr.write('signing_failed');
  process.exitCode = 1;
}
