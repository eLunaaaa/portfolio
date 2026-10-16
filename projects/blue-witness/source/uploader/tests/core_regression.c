/* 파일 역할: 업로더 영속 큐와 ACK 신뢰 경계를 실제 C 구현으로 검사합니다.
 * 입력: 임시 SQLite 경로. 출력: 중복/복구/완료/잘못된 ACK 검사 결과.
 * 이유: HTTP 성공만으로 영상을 지우거나 재시작 후 중복 작업을 만드는 회귀를 막습니다.
 */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "uploader_queue.h"
#include "uploader_ack.h"
int main(int argc,char **argv) {
    assert(argc==2);
    queue_db_t db={0};char error[256]={0};queue_enqueue_t request={0};queue_job_t job={0};
    int64_t id=0,duplicate_id=0;int inserted=0,found=0,recovered=0;uint32_t delay=0;
    strcpy(request.job_id,"EVT_TEST-CAR_TEST");strcpy(request.video_path,"/tmp/test-video.mp4");strcpy(request.metadata_path,"/tmp/test-metadata.json");
    assert(queue_db_open(&db,argv[1],error,sizeof(error))==0);
    assert(queue_db_enqueue(&db,&request,&id,&inserted,error,sizeof(error))==0 && inserted==1);
    assert(queue_db_enqueue(&db,&request,&duplicate_id,&inserted,error,sizeof(error))==0 && inserted==0 && id==duplicate_id);
    assert(queue_db_claim_next(&db,&job,&found,&delay,error,sizeof(error))==0 && found==1);
    queue_db_close(&db);
    assert(queue_db_open(&db,argv[1],error,sizeof(error))==0);
    assert(queue_db_recover_uploading(&db,&recovered,error,sizeof(error))==0 && recovered==1);
    assert(queue_db_claim_next(&db,&job,&found,&delay,error,sizeof(error))==0 && found==1 && job.verification_required==1);
    assert(queue_db_mark_done(&db,job.queue_id,error,sizeof(error))==0);
    assert(queue_db_claim_next(&db,&job,&found,&delay,error,sizeof(error))==0 && found==0);
    queue_db_close(&db);
    const char *sha="0000000000000000000000000000000000000000000000000000000000000000";
    const char *good="{\"saved\":true,\"idempotent_replay\":false,\"storage_ack\":true,\"event_id\":\"EVT_TEST\",\"vehicle_id\":\"CAR_TEST\",\"upload_status\":\"STORED\",\"sha256\":\"0000000000000000000000000000000000000000000000000000000000000000\"}";
    server_upload_ack_t ack={0};
    assert(server_upload_ack_validate(good,"EVT_TEST","CAR_TEST",sha,&ack,error,sizeof(error))==0);
    assert(server_upload_ack_validate(good,"OTHER_EVENT","CAR_TEST",sha,&ack,error,sizeof(error))!=0);
    assert(server_upload_ack_validate("{\"saved\":true}","EVT_TEST","CAR_TEST",sha,&ack,error,sizeof(error))!=0);
    assert(server_health_ack_validate("{\"status\":\"ok\",\"database\":\"error\"}",error,sizeof(error))!=0);
    puts("Uploader queue duplicate/restart/ACK mismatch regression PASS");return 0;
}
