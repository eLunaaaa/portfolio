#ifndef UPLOADER_WATCHDOG_H
#define UPLOADER_WATCHDOG_H

#include <stdint.h>

/* The caller's Ingest/Queue/Worker slot advances only after useful work or when
 * entering a bounded wait. It never changes Queue request ownership. */
void uploader_watchdog_progress(uint32_t timeout_ms);
#define UPLOADER_LOCAL_PROGRESS_MS 60000U

#endif
