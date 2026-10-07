import { readFileSync, readdirSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import type { Plugin } from 'vite'

/** Serve and publish pdf.js resources locally, with their bundled licenses. */
export function pdfAssets(): Plugin {
  const root = dirname(createRequire(import.meta.url).resolve('pdfjs-dist/package.json'))
  const resources = new Map<string, string>([['LICENSE', join(root, 'LICENSE')]])
  for (const directory of ['cmaps', 'standard_fonts', 'wasm']) {
    for (const entry of readdirSync(join(root, directory), { withFileTypes: true })) {
      if (entry.isFile()) resources.set(`${directory}/${entry.name}`, join(root, directory, entry.name))
    }
  }
  return {
    name: 'local-pdf-resources',
    configureServer(server) {
      const prefix = `${server.config.base}pdfjs/`
      server.middlewares.use((request, response, next) => {
        const pathname = request.url?.split('?')[0] || ''
        if (!pathname.startsWith(prefix)) return next()
        const file = resources.get(pathname.slice(prefix.length))
        if (!file) { response.statusCode = 404; response.end(); return }
        if (request.method !== 'GET' && request.method !== 'HEAD') {
          response.statusCode = 405
          response.setHeader('Allow', 'GET, HEAD')
          response.end()
          return
        }
        void readFile(file).then((data) => {
          if (response.destroyed) return
          response.setHeader('Content-Type', file.endsWith('.js') ? 'application/javascript'
            : file.endsWith('.wasm') ? 'application/wasm' : 'application/octet-stream')
          response.setHeader('Cache-Control', 'no-cache')
          response.setHeader('Content-Length', data.length)
          response.end(request.method === 'HEAD' ? undefined : data)
        }, next)
      })
    },
    generateBundle() {
      for (const [name, file] of resources) {
        this.emitFile({ type: 'asset', fileName: `pdfjs/${name}`, source: readFileSync(file) })
      }
    },
  }
}
