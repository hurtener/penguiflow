import { existsSync } from 'node:fs';
import { resolve } from 'node:path';

const reportPath = resolve('dist/bundle-stats.html');

if (!existsSync(reportPath)) {
  console.log('Bundle report not found. Run `npm run build:analyze` first.');
  process.exit(1);
}

console.log(`Bundle report: ${reportPath}`);
