#ifndef PROTOCOL_H
#define PROTOCOL_H

#include <stdint.h>

/*==================================================
 * IPC Message Type
 *==================================================*/
typedef enum
{
    /*--------------- Sensor (100) ----------------*/
    IPC_MSG_SENSOR_DATA          = 100,
    IPC_MSG_SENSOR_STATUS        = 101,

    /*----------------- P2P (200) -----------------*/
    IPC_MSG_ACCIDENT_EVENT       = 200,   // Sensor -> P2P
    IPC_MSG_ACCIDENT_CONFIRM     = 201,   // P2P -> Filter
    IPC_MSG_P2P_STATUS           = 202,
    IPC_MSG_VIDEO_PROCESSOR_EVENT = 203,  // Accident Sensor -> Video Processor

    /*--------------- Filter (300) ---------------*/
    IPC_MSG_FILTER_RESULT        = 300,
    IPC_MSG_INFERENCE_EVENT      = 301,   // Filter -> AI Inference

    /*--------------- Camera (400) ---------------*/
    IPC_MSG_CAMERA_PROTECT       = 400,
    IPC_MSG_CAMERA_READY         = 401,
    IPC_MSG_CAMERA_ERROR         = 402,

    /*------------------ AI (500) ----------------*/
    IPC_MSG_AI_RESULT            = 500,

    /*------------ Time Sync (600) -------------*/
    IPC_MSG_TIME_SYNC_REQUEST     = 600,
    IPC_MSG_TIME_SYNC_STATUS      = 601,

    /*--------------- System (900) ---------------*/
    IPC_MSG_HEARTBEAT            = 900,
    IPC_MSG_SHUTDOWN             = 901,
    IPC_MSG_ERROR                = 902

} ipc_msg_type_t;

/*==================================================
 * IPC Flags
 *==================================================*/
typedef enum
{
    IPC_FLAG_NONE             = 0x00,

    IPC_FLAG_ACK_REQUIRED     = 0x01,
    IPC_FLAG_URGENT           = 0x02,
    IPC_FLAG_RETRY            = 0x04,
    IPC_FLAG_RESERVED         = 0x08

} ipc_flag_t;


/*==================================================
 * IPC Header
 *==================================================*/
typedef struct
{
    uint16_t type;          // ipc_msg_type_t
    uint16_t flags;         // ipc_flag_t (Bit Mask)

    uint32_t length;        // Payload Size (Byte)

    uint32_t sequence;      // Message Sequence Number

    uint64_t timestamp;     // CLOCK_MONOTONIC (ns)

} ipc_header_t;

#endif
