/**
 * ChunkUploader - Resilient, slice-based chunked file uploader for FuraOJ.
 * Bypasses Cloudflare & Nginx body limits (default 5MB slices).
 * Features: Auto-retry on network blips, real-time progress & speed reporting,
 * and a drop-in file input widget helper.
 */
class ChunkUploader {
    constructor(options = {}) {
        this.chunkSize = options.chunkSize || 5 * 1024 * 1024; // 5MB Cloudflare-safe
        this.chunkUrl = options.chunkUrl || '/upload/chunk/';
        this.completeUrl = options.completeUrl || '/upload/complete/';
        this.maxRetries = options.maxRetries !== undefined ? options.maxRetries : 3;
        this.retryDelay = options.retryDelay || 1500;
    }

    static getCsrfToken() {
        if (typeof $ !== 'undefined' && $.cookie && $.cookie('csrftoken')) {
            return $.cookie('csrftoken');
        }
        const match = document.cookie.match(/csrftoken=([^;]+)/);
        if (match) {
            return decodeURIComponent(match[1]);
        }
        const csrfInput = document.querySelector('input[name=csrfmiddlewaretoken]');
        if (csrfInput) {
            return csrfInput.value;
        }
        return window.CSRF_TOKEN || '';
    }

    static formatBytes(bytes, decimals = 1) {
        if (!bytes || bytes === 0) return '0 B';
        const k = 1024;
        const dm = decimals < 0 ? 0 : decimals;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
    }

    static generateUUID() {
        if (typeof crypto !== 'undefined' && crypto.randomUUID) {
            return crypto.randomUUID();
        }
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
            const r = (Math.random() * 16) | 0;
            const v = c === 'x' ? r : (r & 0x3) | 0x8;
            return v.toString(16);
        });
    }

    async uploadChunkWithRetry(formData, signal, attempt = 1) {
        try {
            const response = await fetch(this.chunkUrl, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': ChunkUploader.getCsrfToken(),
                },
                body: formData,
                signal: signal,
            });

            if (!response.ok) {
                const isTransient = [502, 503, 504, 520, 521, 522, 524].includes(response.status);
                if (attempt <= this.maxRetries && (isTransient || response.status >= 500)) {
                    await new Promise(r => setTimeout(r, this.retryDelay * attempt));
                    return await this.uploadChunkWithRetry(formData, signal, attempt + 1);
                }
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.error || `Chunk upload failed with HTTP ${response.status}`);
            }

            return await response.json();
        } catch (err) {
            if (err.name === 'AbortError') throw err;
            if (attempt <= this.maxRetries) {
                await new Promise(r => setTimeout(r, this.retryDelay * attempt));
                return await this.uploadChunkWithRetry(formData, signal, attempt + 1);
            }
            throw err;
        }
    }

    /**
     * Upload a file in 5MB slices.
     * @param {File} file 
     * @param {Object} options callbacks: onProgress, signal
     * @returns {Promise<{ upload_token: string, filename: string, size: number }>}
     */
    async uploadFile(file, options = {}) {
        const uploadId = ChunkUploader.generateUUID();
        const totalSize = file.size;
        const totalChunks = Math.max(1, Math.ceil(totalSize / this.chunkSize));
        const onProgress = options.onProgress || (() => {});
        const signal = options.signal || null;

        let bytesUploaded = 0;

        for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex++) {
            if (signal && signal.aborted) {
                throw new DOMException('Upload aborted by user', 'AbortError');
            }

            const start = chunkIndex * this.chunkSize;
            const end = Math.min(totalSize, start + this.chunkSize);
            const chunkBlob = file.slice(start, end);

            const formData = new FormData();
            formData.append('upload_id', uploadId);
            formData.append('chunk_index', chunkIndex);
            formData.append('total_chunks', totalChunks);
            formData.append('chunk_file', chunkBlob, `chunk_${chunkIndex}`);

            await this.uploadChunkWithRetry(formData, signal);

            bytesUploaded += (end - start);
            const percent = totalSize > 0 ? Math.min(100, Math.round((bytesUploaded / totalSize) * 100)) : 100;

            onProgress({
                percent: percent,
                loaded: bytesUploaded,
                total: totalSize,
                currentChunk: chunkIndex + 1,
                totalChunks: totalChunks,
                formattedLoaded: ChunkUploader.formatBytes(bytesUploaded),
                formattedTotal: ChunkUploader.formatBytes(totalSize),
            });
        }

        // Call complete endpoint
        const completeData = new FormData();
        completeData.append('upload_id', uploadId);
        completeData.append('filename', file.name);
        completeData.append('total_chunks', totalChunks);

        const completeResp = await fetch(this.completeUrl, {
            method: 'POST',
            headers: {
                'X-CSRFToken': ChunkUploader.getCsrfToken(),
            },
            body: completeData,
            signal: signal,
        });

        if (!completeResp.ok) {
            const errData = await completeResp.json().catch(() => ({}));
            throw new Error(errData.error || `Completing upload failed with HTTP ${completeResp.status}`);
        }

        return await completeResp.json();
    }

    /**
     * Binds a file input to chunked upload with UI progress bar.
     * @param {HTMLInputElement|string} inputSelector
     * @param {Object} options
     */
    static attachToFileInput(inputSelector, options = {}) {
        const input = typeof inputSelector === 'string' ? document.querySelector(inputSelector) : inputSelector;
        if (!input) return null;

        const uploader = new ChunkUploader(options);
        const tokenInputName = options.tokenInputName || `${input.name || 'file'}-token`;
        const autoSubmit = options.autoSubmit || false;
        const form = input.closest('form');

        // Check or create hidden input for token
        let hiddenTokenInput = form ? form.querySelector(`input[name="${tokenInputName}"]`) : null;
        if (!hiddenTokenInput && form) {
            hiddenTokenInput = document.createElement('input');
            hiddenTokenInput.type = 'hidden';
            hiddenTokenInput.name = tokenInputName;
            form.appendChild(hiddenTokenInput);
        }

        // Create UI progress widget
        const widget = document.createElement('div');
        widget.className = 'chunk-upload-progress';
        widget.style.display = 'none';
        widget.innerHTML = `
            <div class="chunk-progress-track">
                <div class="chunk-progress-bar" style="width: 0%;"></div>
            </div>
            <div class="chunk-progress-info">
                <span class="chunk-progress-status">Uploading...</span>
                <span class="chunk-progress-percent">0%</span>
                <span class="chunk-progress-detail">(0 B / 0 B)</span>
                <button type="button" class="chunk-progress-cancel" title="Cancel upload">&times;</button>
            </div>
            <div class="chunk-progress-error" style="display: none;"></div>
        `;

        input.parentNode.insertBefore(widget, input.nextSibling);

        const progressBar = widget.querySelector('.chunk-progress-bar');
        const progressStatus = widget.querySelector('.chunk-progress-status');
        const progressPercent = widget.querySelector('.chunk-progress-percent');
        const progressDetail = widget.querySelector('.chunk-progress-detail');
        const progressError = widget.querySelector('.chunk-progress-error');
        const cancelBtn = widget.querySelector('.chunk-progress-cancel');

        let abortController = null;

        cancelBtn.addEventListener('click', () => {
            if (abortController) {
                abortController.abort();
            }
            widget.style.display = 'none';
            input.value = '';
            if (hiddenTokenInput) hiddenTokenInput.value = '';
            if (options.submitButton) options.submitButton.disabled = false;
        });

        input.addEventListener('change', async (e) => {
            const file = input.files && input.files[0];
            if (!file) {
                widget.style.display = 'none';
                if (hiddenTokenInput) hiddenTokenInput.value = '';
                return;
            }

            // Abort previous upload if active
            if (abortController) {
                abortController.abort();
            }
            abortController = new AbortController();

            // Reset UI
            widget.style.display = 'block';
            progressError.style.display = 'none';
            progressError.textContent = '';
            progressBar.style.width = '0%';
            progressBar.classList.remove('success', 'failed');
            progressStatus.textContent = 'Uploading...';
            progressPercent.textContent = '0%';
            progressDetail.textContent = `(0 B / ${ChunkUploader.formatBytes(file.size)})`;

            // Disable submit button while uploading chunks
            const submitBtn = options.submitButton || (form ? form.querySelector('button[type="submit"], input[type="submit"]') : null);
            if (submitBtn) {
                submitBtn.disabled = true;
                submitBtn.dataset.originalText = submitBtn.textContent || submitBtn.value;
                if (submitBtn.tagName === 'INPUT') {
                    submitBtn.value = 'Uploading file chunks...';
                } else {
                    submitBtn.textContent = 'Uploading file chunks...';
                }
            }

            try {
                const result = await uploader.uploadFile(file, {
                    signal: abortController.signal,
                    onProgress: (p) => {
                        progressBar.style.width = `${p.percent}%`;
                        progressPercent.textContent = `${p.percent}%`;
                        progressDetail.textContent = `(${p.formattedLoaded} / ${p.formattedTotal}) [${p.currentChunk}/${p.totalChunks}]`;
                    },
                });

                // Completed
                progressBar.style.width = '100%';
                progressBar.classList.add('success');
                progressStatus.textContent = 'Upload complete!';
                progressPercent.textContent = '100%';
                if (hiddenTokenInput) {
                    hiddenTokenInput.value = result.upload_token;
                }

                if (options.onComplete) {
                    options.onComplete(result);
                }

                if (submitBtn) {
                    submitBtn.disabled = false;
                    if (submitBtn.dataset.originalText) {
                        if (submitBtn.tagName === 'INPUT') {
                            submitBtn.value = submitBtn.dataset.originalText;
                        } else {
                            submitBtn.textContent = submitBtn.dataset.originalText;
                        }
                    }
                }

                if (autoSubmit && form) {
                    form.submit();
                }
            } catch (err) {
                if (err.name === 'AbortError') {
                    progressStatus.textContent = 'Upload cancelled';
                } else {
                    progressBar.classList.add('failed');
                    progressStatus.textContent = 'Upload failed';
                    progressError.style.display = 'block';
                    progressError.textContent = err.message || 'An error occurred during chunked upload.';
                    if (options.onError) {
                        options.onError(err);
                    }
                }
                if (submitBtn) {
                    submitBtn.disabled = false;
                    if (submitBtn.dataset.originalText) {
                        if (submitBtn.tagName === 'INPUT') {
                            submitBtn.value = submitBtn.dataset.originalText;
                        } else {
                            submitBtn.textContent = submitBtn.dataset.originalText;
                        }
                    }
                }
            }
        });

        return { uploader, widget };
    }
}

if (typeof window !== 'undefined') {
    window.ChunkUploader = ChunkUploader;
}
