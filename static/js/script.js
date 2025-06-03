document.addEventListener('DOMContentLoaded', function() {
    const downloadLinks = document.querySelectorAll('.download-link');
    const progressSection = document.getElementById('progressSection');
    const progressBar = document.getElementById('progressBar');
    const progressStatus = document.getElementById('progressStatus');
    const progressFilename = document.getElementById('progressFilename');
    let eventSource = null;

    // Initialize Materialize components
    var sidenavElems = document.querySelectorAll('.sidenav');
    M.Sidenav.init(sidenavElems);
    var selectElems = document.querySelectorAll('select');
    M.FormSelect.init(selectElems);
    var cardRevealElems = document.querySelectorAll('.card'); // For card reveal
    cardRevealElems.forEach(card => {
        // No specific init for card reveal, it works by class structure
    });


    downloadLinks.forEach(link => {
        link.addEventListener('click', function(event) {
            // event.preventDefault(); // Prevent default download temporarily for SSE setup
                                   // We will trigger it manually after SSE is set up

            const filename = this.dataset.filename || 'downloading_file...';
            progressFilename.textContent = `Downloading: ${filename}`;
            progressSection.style.display = 'block';
            progressBar.style.width = '0%';
            progressStatus.textContent = 'Initializing download...';

            // Close existing EventSource if any
            if (eventSource) {
                eventSource.close();
            }

            // Extract video_id and itag from the href
            const urlParams = new URL(this.href).searchParams;
            const videoId = urlParams.get('video_id');
            const itag = urlParams.get('itag');
            const type = urlParams.get('type');

            // Construct the URL for progress updates
            // The actual download will be triggered by the href of the link
            // The progress endpoint is just for monitoring
            const progressUrl = `/progress/${videoId}/${itag}/${type}`;

            eventSource = new EventSource(progressUrl);

            eventSource.onmessage = function(e) {
                const data = JSON.parse(e.data);
                if (data.error) {
                    progressBar.style.width = '100%';
                    progressBar.classList.remove('red', 'darken-2');
                    progressBar.classList.add('red', 'lighten-1'); // Error color
                    progressStatus.textContent = `Error: ${data.error}`;
                    M.toast({html: `Error: ${data.error}`, classes: 'red rounded'});
                    eventSource.close();
                } else if (data.progress !== undefined) {
                    const percent = Math.round(data.progress);
                    progressBar.style.width = percent + '%';
                    progressStatus.textContent = `${percent}% complete (${(data.downloaded_mb || 0).toFixed(2)} MB / ${(data.total_mb || 0).toFixed(2)} MB)`;
                    if (percent === 100) {
                        progressStatus.textContent = `Download complete! Processing...`;
                        M.toast({html: 'Download complete! File saved.', classes: 'green rounded'});
                        // Don't close eventSource here, wait for 'completed' or 'error' message from server for finality
                    }
                } else if (data.status === 'completed') {
                    progressBar.style.width = '100%';
                    progressStatus.textContent = `Download finished: ${data.filename}`;
                    M.toast({html: `Finished: ${data.filename}`, classes: 'green rounded'});
                    eventSource.close();
                    // Hide progress bar after a short delay
                    setTimeout(() => {
                        progressSection.style.display = 'none';
                        progressBar.style.width = '0%';
                    }, 5000);
                }
            };

            eventSource.onerror = function(e) {
                console.error("EventSource failed:", e);
                progressStatus.textContent = 'Connection error or download stream ended.';
                // M.toast({html: 'Progress connection lost.', classes: 'orange rounded'});
                // Don't close immediately, server might signal completion still or an error
                // If it was a real error, the onmessage with data.error should catch it.
                // If it's just the stream ending, the 'completed' status will handle it.
            };

            // Important: Do NOT preventDefault on the click if you want the browser
            // to handle the actual file download via the href.
            // The SSE is for *monitoring* the download happening on the server *before* send_file.
            // If you AREN'T using send_file and downloading via AJAX, then you'd preventDefault.
            // Since we ARE using send_file, the browser handles the download triggered by the 'a' tag's href.
            // The progress endpoint needs to be hit *before* the actual download route,
            // or simultaneously, which is tricky.
            //
            // A common pattern:
            // 1. Click link -> JS makes an AJAX call to INITIATE download on server (returns a download ID).
            // 2. JS then starts polling SSE with this download ID.
            // 3. The INITIATE route on server starts download in background thread.
            // 4. Once download is complete on server, the INITIATE route (or another route) provides a link to the actual file.
            //
            // For simplicity with send_file and direct link click:
            // The progress SSE will track the server's download *from YouTube*.
            // Once that's done, `send_file` starts, and the browser's native download progress takes over.
            // So, this progress bar is for "Server fetching from YouTube".
        });
    });

    // Clear URL on page load if it's from a previous submission to avoid confusion
    const urlInput = document.getElementById('youtube_url');
    if (performance.getEntriesByType("navigation")[0].type === "reload") {
        // This doesn't quite work as expected for clearing form on simple reload.
        // A better way is server-side, not passing youtube_url back on GET requests unless intended.
    }
    // Or, clear it on focus if it's the example URL.
    // urlInput.addEventListener('focus', function() {
    //     if (this.value === 'https://www.youtube.com/watch?v=dQw4w9WgXcQ') { // Example
    //         this.value = '';
    //     }
    // });
});