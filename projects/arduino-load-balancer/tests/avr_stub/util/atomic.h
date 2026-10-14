#pragma once
#define ATOMIC_RESTORESTATE 0
#define ATOMIC_BLOCK(mode) for (bool once = true; once; once = false)
