/* PC 단일 실행 흐름에서 ATOMIC_BLOCK 문법을 제공하는 대역입니다. 실제 interrupt 제어는 수행하지 않으며 같은 블록을 한 번 실행합니다. */

#pragma once
#define ATOMIC_RESTORESTATE 0
#define ATOMIC_BLOCK(mode) for (bool once = true; once; once = false)
