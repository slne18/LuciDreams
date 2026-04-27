import type { Directory } from '@capacitor/filesystem';
export declare type Base64String = string;
export interface RecordingData {
    value: {
        recordDataBase64?: Base64String;
        msDuration: number;
        mimeType: string;
        path?: string;
    };
}
export declare type RecordingOptions = never | {
    directory: Directory;
    subDirectory?: string;
};
export interface GenericResponse {
    value: boolean;
}
export declare const RecordingStatus: {
    readonly RECORDING: "RECORDING";
    readonly PAUSED: "PAUSED";
    readonly NONE: "NONE";
};
export interface CurrentRecordingStatus {
    status: (typeof RecordingStatus)[keyof typeof RecordingStatus];
}
export interface VoiceRecorderPlugin {
    canDeviceVoiceRecord(): Promise<GenericResponse>;
    requestAudioRecordingPermission(): Promise<GenericResponse>;
    hasAudioRecordingPermission(): Promise<GenericResponse>;
    startRecording(options?: RecordingOptions): Promise<GenericResponse>;
    stopRecording(): Promise<RecordingData>;
    pauseRecording(): Promise<GenericResponse>;
    resumeRecording(): Promise<GenericResponse>;
    getCurrentStatus(): Promise<CurrentRecordingStatus>;
}
